"""Turn per-resource decisions into the ordered steps_json contract."""
from __future__ import annotations

import csv
from dataclasses import dataclass

from config import DATASET

# Operation order inside each phase, exactly as listed in problem_statement.md "Allowed Operations".
FINDING_ORDER = ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"]
REMEDIATION_ORDER = ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"]
VERIFICATION_ORDER = ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED",
                      "VERIFY_RECORD_ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]
OP_STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE", "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED", "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
PHASES = [("FINDING", FINDING_ORDER), ("REMEDIATION", REMEDIATION_ORDER), ("VERIFICATION", VERIFICATION_ORDER)]


def load_rules() -> dict[str, str]:
    """data_category -> required action (DELETE / REDACT / ANONYMIZE / RETAIN), from deletion_rules.csv."""
    with open(DATASET / "deletion_rules.csv", encoding="utf-8") as f:
        return {r["data_category"]: r["required_action"] for r in csv.DictReader(f)}


@dataclass
class Decision:
    """What the audit concluded about one resource at audit_at."""
    system_id: str
    resource_id: str
    finding: str | None        # FINDING op, or None when already compliant
    remediation: str | None
    verification: str
    evidence_id: str           # evidence proving the current state
    observed: bool = True      # False: linked but no state evidence -> future check only


# required action -> (finding, remediation, verification) when the record is NOT yet compliant,
# and the verification used when it already is.
def decide(category: str, action: str, state: str) -> tuple[str | None, str | None, str] | None:
    if category == "DATA_FLOW":
        if state == "FLOW_ACTIVE":
            return "REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"
        return None, None, "VERIFY_DATA_FLOW_DISABLED"
    if category == "BACKUP_ARCHIVE":
        if state == "RESTORE_ALLOWED":
            return "REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"
        return None, None, "VERIFY_RESTORE_BLOCKED"
    if action == "RETAIN":
        return None, None, "VERIFY_RECORD_PRESENT"
    if action == "DELETE":
        if state in ("IDENTIFIABLE", "DEIDENTIFIED", "PRESENT"):
            return "REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"
        return None, None, "VERIFY_RECORD_ABSENT"
    if action == "REDACT":
        if state == "IDENTIFIABLE":
            return "REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"
        return None, None, "VERIFY_RECORD_REDACTED"
    if action == "ANONYMIZE":
        if state == "IDENTIFIABLE":
            return "REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"
        return None, None, "VERIFY_RECORD_ANONYMIZED"
    return None


def build_steps(decisions: list[Decision]) -> tuple[str, list[dict]]:
    incomplete = any(d.finding for d in decisions)
    rows: list[tuple[str, str, str, str, str]] = []   # phase, op, system, resource, evidence
    for d in decisions:
        if not d.observed and not incomplete:
            continue                   # unproven state cannot back a COMPLETE verification
        if incomplete:
            if d.finding:
                rows.append(("FINDING", d.finding, d.system_id, d.resource_id, d.evidence_id))
                rows.append(("REMEDIATION", d.remediation, d.system_id, d.resource_id, ""))
            rows.append(("VERIFICATION", d.verification, d.system_id, d.resource_id, ""))
        else:
            rows.append(("VERIFICATION", d.verification, d.system_id, d.resource_id, d.evidence_id))
    steps = []
    for phase, order in PHASES:
        phase_rows = sorted((r for r in rows if r[0] == phase), key=lambda r: (order.index(r[1]), r[2], r[3]))
        for _, op, system, resource, evidence in phase_rows:
            steps.append({"step_no": len(steps) + 1, "phase": phase, "system_id": system,
                          "resource_id": resource, "operation": op, "state": OP_STATE[op],
                          "evidence_id": evidence})
    return ("INCOMPLETE" if incomplete else "COMPLETE"), steps
