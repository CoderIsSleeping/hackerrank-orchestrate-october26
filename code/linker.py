"""Global resource linker.

Deletion-workflow activity rows name a resource but never the person. Several
workflows run in the same minutes, so a single case cannot tell its own events
apart from its neighbours'. All requests are known up front, though, so the
events are attributed *jointly*:

1. Candidate edges: event e may belong to case c if it happened inside c's
   workflow window (requested_at .. reported_at + WINDOW_AFTER).
2. Grounded edges: if evidence available at c's audit_at ties e's resource to
   c's identifiers, e belongs to c and is removed from every other case.
3. Exclusion: a resource grounded to a different subject never belongs to c.
4. One resource per type per case: once c owns an account_* event, it no
   longer competes for other account_* events.
5. Propagate until stable: an event left with exactly one candidate case is
   assigned to it.

Events that stay ambiguous are left unlinked: the solver does not guess.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from evidence_extract import Unit
from identity import IdentityIndex, Subject, text_tokens

WORKFLOW_ACTORS = {"deletion_worker"}

# Each workflow event template lands at a characteristic offset from its case's reported_at.
# Offsets observed on the 32 public samples (minutes), padded by ~10 min on each side:
#   primary profile submission   -75 .. -45
#   policy action completed      -29 .. -7
#   final result / status checks +39 .. +114
#   single-step workflow report  -6  .. +10
TEMPLATES = [
    ("primary", "submitted primary profile", -85, -35),
    ("policy", "Policy action completed", -40, 5),
    ("followup", "received the final result", 25, 125),
    ("followup", "status check confirmed", 25, 125),
    ("followup", "check confirmed", 25, 125),
    ("single", "Target resource", -20, 20),
]
# Fallback for templates not seen in the samples: the union of the windows above.
DEFAULT_WINDOW = (-90, 125)


def _t(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


def rtype(resource_id: str) -> str:
    return resource_id.rsplit("_", 1)[0]


def template_of(details: str) -> tuple[str, int, int]:
    for name, needle, lo, hi in TEMPLATES:
        if needle in details:
            return name, lo, hi
    return "other", *DEFAULT_WINDOW


def slot_of(event: dict) -> str:
    """Workflow slot: one primary profile per case, otherwise one resource per type."""
    name, _, _ = template_of(event["details"])
    return "primary" if name == "primary" else rtype(event["resource_id"])


class GlobalLinker:
    def __init__(self, requests: list[dict], units: list[Unit], activity: list[dict], identity: IdentityIndex):
        self.requests = {r["case_id"]: r for r in requests}
        self.subjects: dict[str, Subject] = {c: identity.resolve(r["email"])[0] for c, r in self.requests.items()}
        self.units = units
        self.activity = activity
        self.grounded: dict[str, dict[str, str]] = {}      # case -> resource -> evidence/activity id
        self.assigned: dict[str, dict[str, str]] = defaultdict(dict)   # case -> resource -> activity id
        self._ground_all()
        self._solve()

    # --------------------------------------------------------------- grounding

    def _ground_all(self) -> None:
        # Inverted index: identifier token -> units / link activities containing it.
        by_token: dict[str, list] = defaultdict(list)
        for u in self.units:
            for tok in text_tokens(u.text):
                by_token[tok].append(("unit", u))
        for a in self.activity:
            if a["event_type"] == "LINK_RESOURCE_IDENTITY":
                for tok in text_tokens(a["details"]):
                    by_token[tok].append(("activity", a))
        for case_id, req in self.requests.items():
            subject, audit_at = self.subjects[case_id], req["audit_at"]
            links: dict[str, str] = {}
            if subject.user_id is not None:
                hits = [h for tok in subject.tokens() for h in by_token.get(tok, [])]
                for kind, x in sorted(hits, key=lambda h: (h[1].observed_at, h[1].file_id) if h[0] == "unit"
                                      else (h[1]["event_time"], h[1]["activity_id"])):
                    if kind == "unit" and x.captured_at <= audit_at and x.observed_at <= audit_at:
                        links.setdefault(x.resource_id, x.file_id)
                    elif kind == "activity" and x["event_time"] <= audit_at:
                        links.setdefault(x["resource_id"], x["activity_id"])
            self.grounded[case_id] = links

    # --------------------------------------------------------------- solver

    def _solve(self) -> None:
        owners_by_resource: dict[str, set] = defaultdict(set)   # resource -> subjects grounded to it
        for case_id, links in self.grounded.items():
            for rid in links:
                owners_by_resource[rid].add(self.subjects[case_id].user_id)

        events = [a for a in self.activity if a["source_system"] in WORKFLOW_ACTORS]
        candidates: dict[str, set] = {}
        for a in events:
            t = _t(a["event_time"])
            _, lo, hi = template_of(a["details"])
            cands = set()
            for case_id, req in self.requests.items():
                offset = (t - _t(req["reported_at"])).total_seconds() / 60
                if _t(req["requested_at"]) <= t <= _t(req["audit_at"]) and lo <= offset <= hi:
                    owners = owners_by_resource.get(a["resource_id"])
                    uid = self.subjects[case_id].user_id
                    if owners and uid not in owners:
                        continue                      # rule 3: grounded to somebody else
                    cands.add(case_id)
            if cands:
                candidates[a["activity_id"]] = cands
        by_id = {a["activity_id"]: a for a in events}

        # Rule 2: grounded events are fixed.
        for aid, cands in candidates.items():
            rid = by_id[aid]["resource_id"]
            grounded_cases = {c for c in cands if rid in self.grounded[c]}
            if grounded_cases:
                candidates[aid] = grounded_cases

        filled: set = set()          # (case, slot) pairs already owned

        def assign(c: str, aid: str) -> None:
            self.assigned[c][by_id[aid]["resource_id"]] = aid
            filled.add((c, slot_of(by_id[aid])))

        # Grounded events claim their slots before any ungrounded event is considered.
        for aid, cands in candidates.items():
            rid = by_id[aid]["resource_id"]
            if len(cands) == 1 and rid in self.grounded[next(iter(cands))]:
                assign(next(iter(cands)), aid)

        changed = True
        while changed:
            changed = False
            # Event side: an event with a single remaining candidate case belongs to it.
            for aid, cands in candidates.items():
                rid, slot = by_id[aid]["resource_id"], slot_of(by_id[aid])
                live = {c for c in cands if (c, slot) not in filled or rid in self.assigned[c]}
                if live != cands:
                    candidates[aid] = live
                    changed = True
                if len(live) == 1:
                    c = next(iter(live))
                    if rid not in self.assigned[c]:
                        assign(c, aid)
                        changed = True
            # Case side: every workflow submits exactly one primary profile, so a case with a
            # single remaining primary-profile candidate owns it.
            open_primary: dict[str, list] = defaultdict(list)
            for aid, cands in candidates.items():
                if slot_of(by_id[aid]) == "primary":
                    for c in cands:
                        if (c, "primary") not in filled:
                            open_primary[c].append(aid)
            for c, aids in open_primary.items():
                unowned = [aid for aid in aids if not any(by_id[aid]["resource_id"] in self.assigned[o] for o in candidates[aid])]
                if len(unowned) == 1:
                    assign(c, unowned[0])
                    candidates[unowned[0]] = {c}
                    changed = True

    def links_for(self, case_id: str) -> dict[str, str]:
        out = dict(self.grounded.get(case_id, {}))
        for rid, aid in self.assigned.get(case_id, {}).items():
            out.setdefault(rid, aid)
        return out
