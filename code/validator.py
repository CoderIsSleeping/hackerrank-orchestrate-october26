"""Contract checks run on every row before output.csv is written. Any violation stops the run."""
from __future__ import annotations

from planner import OP_STATE, PHASES

STEP_KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
PHASE_OPS = {phase: set(order) for phase, order in PHASES}


def validate_row(case_id: str, status: str, steps: list[dict], audit_at: str,
                 evidence_times: dict[str, str]) -> list[str]:
    errors = []
    if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
        errors.append(f"{case_id}: bad status {status}")
    if status == "NOT_APPLICABLE" and steps:
        errors.append(f"{case_id}: NOT_APPLICABLE must have []")
    if status == "COMPLETE" and any(s["phase"] != "VERIFICATION" for s in steps):
        errors.append(f"{case_id}: COMPLETE may only contain verification steps")
    if status == "INCOMPLETE" and not any(s["phase"] == "FINDING" for s in steps):
        errors.append(f"{case_id}: INCOMPLETE without findings")
    phase_index = {p: i for i, (p, _) in enumerate(PHASES)}
    last_phase = -1
    for i, s in enumerate(steps, 1):
        if set(s) != STEP_KEYS:
            errors.append(f"{case_id}: step {i} keys {sorted(s)}")
            continue
        if s["step_no"] != i:
            errors.append(f"{case_id}: step_no {s['step_no']} at position {i}")
        if s["operation"] not in PHASE_OPS.get(s["phase"], set()):
            errors.append(f"{case_id}: {s['operation']} not allowed in {s['phase']}")
        if OP_STATE.get(s["operation"]) != s["state"]:
            errors.append(f"{case_id}: {s['operation']} with state {s['state']}")
        if phase_index[s["phase"]] < last_phase:
            errors.append(f"{case_id}: phase order broken at step {i}")
        last_phase = phase_index[s["phase"]]
        future = status == "INCOMPLETE" and s["phase"] in ("REMEDIATION", "VERIFICATION")
        if future and s["evidence_id"] != "":
            errors.append(f"{case_id}: future step {i} must have empty evidence_id")
        if not future:
            if not s["evidence_id"]:
                errors.append(f"{case_id}: step {i} needs evidence")
            elif s["evidence_id"].startswith("evidence_") and evidence_times.get(s["evidence_id"], "9999") > audit_at:
                errors.append(f"{case_id}: step {i} cites {s['evidence_id']} captured after audit_at")
    return errors
