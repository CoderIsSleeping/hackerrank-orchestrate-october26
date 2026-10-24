"""Per-case investigation: identifiers -> linked resources -> state at audit_at -> decisions."""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field

from config import DATASET, RESOURCE_PREFIXES
from evidence_extract import Scope, Unit
from identity import IdentityIndex, Subject
from linker import GlobalLinker
from planner import Decision, decide, load_rules
from state import infer_category, interpret


def resource_meta(resource_id: str) -> tuple[str, str]:
    prefix = resource_id.rsplit("_", 1)[0]
    return RESOURCE_PREFIXES.get(prefix, ("unknown_service", "UNKNOWN"))


@dataclass
class Observation:
    time: str
    captured_at: str
    state: str
    evidence_id: str
    origin: str          # "unit" or "scope"


@dataclass
class CaseResult:
    case_id: str
    subject: Subject
    resources: dict = field(default_factory=dict)       # resource_id -> why it was linked
    decisions: list = field(default_factory=list)
    notes: list = field(default_factory=list)


class Investigator:
    def __init__(self, units: list[Unit], scopes: list[Scope]):
        self.identity = IdentityIndex()
        self.rules = load_rules()
        self.units_by_resource: dict[str, list[Unit]] = defaultdict(list)
        for u in units:
            self.units_by_resource[u.resource_id].append(u)
        self.units = units
        self.scopes = scopes
        with open(DATASET / "system_activity.csv", encoding="utf-8") as f:
            self.activity = list(csv.DictReader(f))
        self.links_by_resource: dict[str, list[dict]] = defaultdict(list)
        for a in self.activity:
            if a["event_type"] == "LINK_RESOURCE_IDENTITY":
                self.links_by_resource[a["resource_id"]].append(a)

    def attach_linker(self, requests: list[dict], no_workflow: set | None = None) -> None:
        """Resource linking is solved jointly over every request (see linker.py)."""
        self.linker = GlobalLinker(requests, self.units, self.activity, self.identity, no_workflow)

    # ------------------------------------------------------------ state

    def observations(self, resource_id: str, category: str, audit_at: str) -> list[Observation]:
        obs = []
        for u in self.units_by_resource.get(resource_id, []):
            if u.captured_at > audit_at or u.observed_at > audit_at:
                continue
            st = interpret(u, category)
            if st:
                obs.append(Observation(u.observed_at, u.captured_at, st, u.file_id, "unit"))
        prefix = resource_id.rsplit("_", 1)[0]
        by_file = defaultdict(list)
        for s in self.scopes:
            if prefix in s.prefixes and s.captured_at <= audit_at and s.file_captured_at <= audit_at:
                by_file[(s.file_id, s.captured_at)].append(s)
        for (fid, t), group in by_file.items():
            if not any(resource_id in s.resource_ids for s in group):
                obs.append(Observation(t, t, _absent_state(category), fid, "scope"))
        # A data-catalog link states that the record exists and belongs to a person. It is the weakest
        # evidence (ranked last) and says nothing about flows or restore settings.
        if category not in ("DATA_FLOW", "BACKUP_ARCHIVE"):
            for a in self.links_by_resource.get(resource_id, []):
                if a["event_time"] <= audit_at:
                    obs.append(Observation(a["event_time"], a["event_time"], "IDENTIFIABLE", a["activity_id"], "catalog"))
        return obs

    def workflow_action(self, resource_id: str, audit_at: str) -> str | None:
        """Policy action the deletion workflow attempted on this resource (a category hint, not proof)."""
        acts = [a for a in self.activity if a["resource_id"] == resource_id and a["event_time"] <= audit_at
                and a["source_system"] == "deletion_worker" and a["event_type"] in WORKFLOW_ACTIONS]
        return WORKFLOW_ACTIONS[acts[-1]["event_type"]] if acts else None

    def investigate(self, req: dict) -> CaseResult:
        subject, _ = self.identity.resolve(req["email"])
        result = CaseResult(req["case_id"], subject)
        result.resources = self.linker.links_for(req["case_id"])
        audit_at = req["audit_at"]
        per_resource = {}
        for rid in sorted(result.resources):
            system, category = resource_meta(rid)
            visible = [u for u in self.units_by_resource.get(rid, []) if u.captured_at <= audit_at]
            category = infer_category(category, visible, set(self.rules))
            per_resource[rid] = (system, category, self.observations(rid, category, audit_at))

        # The case's primary evidence file is the one that observes most of its resources directly.
        # When several observations agree on a state, cite that file rather than a bulk listing.
        file_rank = defaultdict(int)
        for _, _, obs in per_resource.values():
            for fid in {o.evidence_id for o in obs if o.origin == "unit"}:
                file_rank[fid] += 1

        for rid, (system, category, obs) in per_resource.items():
            action = self.rules.get(category, "")
            hinted = self.workflow_action(rid, audit_at)
            if hinted and action in ("DELETE", "REDACT", "ANONYMIZE") and not _has_record_type(obs, self.units_by_resource[rid]):
                action = hinted
            if not obs:
                # Linked (e.g. by its deletion workflow) but never observed: its final state can still be
                # required as a post-remediation check, but it can never support a COMPLETE verdict.
                d = decide(category, action, _absent_state(category) if action != "RETAIN" else "PRESENT")
                if d:
                    result.decisions.append(Decision(system, rid, None, None, d[2], "", observed=False))
                result.notes.append(f"{rid}: no state evidence <= audit_at")
                continue
            # Evidence strength: a record seen directly > its absence from a complete listing > a catalog link.
            for origin in ("unit", "scope", "catalog"):
                ranked = [o for o in obs if o.origin == origin]
                if ranked:
                    break
            latest = max(ranked, key=lambda o: (o.time, o.captured_at))
            same = [o for o in ranked if o.state == latest.state]
            cited = max(same, key=lambda o: (file_rank.get(o.evidence_id, 0), o.time))
            d = decide(category, action, latest.state)
            if d is None:
                result.notes.append(f"{rid}: no rule for {category}")
                continue
            finding, remediation, verification = d
            result.decisions.append(Decision(system, rid, finding, remediation, verification, cited.evidence_id))
        return result


WORKFLOW_ACTIONS = {"DELETE_RECORD": "DELETE", "REDACT_RECORD": "REDACT", "ANONYMIZE_RECORD": "ANONYMIZE"}


def _absent_state(category: str) -> str:
    return {"DATA_FLOW": "FLOW_DISABLED", "BACKUP_ARCHIVE": "RESTORE_BLOCKED"}.get(category, "ABSENT")


def _has_record_type(obs, units) -> bool:
    """True when the evidence itself states the record's category (that outranks a workflow hint)."""
    return any(u.fields.get("record_type") for u in units)
