"""Contract and policy checks on a predictions file (default: the submitted output.csv).

    python code/evaluation/check_output.py [path/to/output.csv] [path/to/requests.csv]

Exit code 1 if any check fails. Every check is listed with its count so a pass is never assumed.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from config import DATASET, OUTPUT_PATH  # noqa: E402
from planner import FINDING_ORDER, OP_STATE, PHASES, REMEDIATION_ORDER, VERIFICATION_ORDER  # noqa: E402

KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
ORDER = {"FINDING": FINDING_ORDER, "REMEDIATION": REMEDIATION_ORDER, "VERIFICATION": VERIFICATION_ORDER}
REMEDIATION_TO_VERIFY = {"DISABLE_DATA_FLOW": "VERIFY_DATA_FLOW_DISABLED", "BLOCK_RESTORE": "VERIFY_RESTORE_BLOCKED",
                         "DELETE_RECORD": "VERIFY_RECORD_ABSENT", "REDACT_RECORD": "VERIFY_RECORD_REDACTED",
                         "ANONYMIZE_RECORD": "VERIFY_RECORD_ANONYMIZED"}
RETAINED_PREFIXES = ("audit_record_", "legal_hold_", "backup_")


def known_evidence_ids() -> dict[str, str]:
    """id -> time it became available (file capture, activity time, audit-log time)."""
    ids = {}
    for name, key, t in (("evidence_files.csv", "file_id", "captured_at"),
                         ("system_activity.csv", "activity_id", "event_time"),
                         ("user_audit_log.csv", "audit_id", "event_time")):
        with open(DATASET / name, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ids[r[key]] = r[t]
    return ids


def check(pred_path: Path, req_path: Path) -> list[tuple[str, int, list]]:
    with open(pred_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        rows = list(reader)
    with open(req_path, encoding="utf-8") as f:
        requests = {r["case_id"]: r for r in csv.DictReader(f)}
    evidence = known_evidence_ids()
    problems: dict[str, list] = {name: [] for name in (
        "header is exactly case_id,deletion_status,steps_json", "one row per request, no duplicates or extras",
        "rows sorted by case_id (numeric)", "no missing values", "deletion_status in allowed set",
        "steps_json is a JSON array of objects with exactly the 7 keys", "step_no consecutive from 1",
        "phase order FINDING > REMEDIATION > VERIFICATION", "operation allowed for its phase, state matches operation",
        "within-phase order: operation order, then system_id, resource_id", "NOT_APPLICABLE has []",
        "COMPLETE: only verification steps, at least one, each citing evidence", "INCOMPLETE: has findings; future steps have empty evidence_id",
        "cited ids exist and were available at audit_at", "every finding has a remediation on the same resource",
        "every remediation has the matching final-state verification", "retained categories never deleted/redacted/anonymized")}

    if header != ["case_id", "deletion_status", "steps_json"]:
        problems["header is exactly case_id,deletion_status,steps_json"].append(header)
    ids = [r["case_id"] for r in rows]
    dup = [c for c, n in Counter(ids).items() if n > 1]
    if dup or set(ids) != set(requests):
        problems["one row per request, no duplicates or extras"].append(
            {"duplicates": dup, "missing": sorted(set(requests) - set(ids))[:5], "extra": sorted(set(ids) - set(requests))[:5]})
    if ids != sorted(ids, key=lambda c: int(c.rsplit("_", 1)[1])):
        problems["rows sorted by case_id (numeric)"].append("order")

    for r in rows:
        cid, status = r["case_id"], r["deletion_status"]
        if any(v in (None, "") for v in r.values()):
            problems["no missing values"].append(cid)
        if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
            problems["deletion_status in allowed set"].append(cid)
        try:
            steps = json.loads(r["steps_json"])
            assert isinstance(steps, list) and all(isinstance(s, dict) and set(s) == KEYS for s in steps)
        except Exception:
            problems["steps_json is a JSON array of objects with exactly the 7 keys"].append(cid)
            continue
        audit_at = requests.get(cid, {}).get("audit_at", "")
        if [s["step_no"] for s in steps] != list(range(1, len(steps) + 1)):
            problems["step_no consecutive from 1"].append(cid)
        phases = [p for p, _ in PHASES]
        if [phases.index(s["phase"]) for s in steps] != sorted(phases.index(s["phase"]) for s in steps):
            problems["phase order FINDING > REMEDIATION > VERIFICATION"].append(cid)
        for s in steps:
            if s["operation"] not in ORDER.get(s["phase"], []) or OP_STATE.get(s["operation"]) != s["state"]:
                problems["operation allowed for its phase, state matches operation"].append(cid)
                break
        for phase, order in ORDER.items():
            ph = [s for s in steps if s["phase"] == phase]
            keyed = [(order.index(s["operation"]), s["system_id"], s["resource_id"]) for s in ph if s["operation"] in order]
            if keyed != sorted(keyed):
                problems["within-phase order: operation order, then system_id, resource_id"].append(cid)
        if status == "NOT_APPLICABLE" and steps:
            problems["NOT_APPLICABLE has []"].append(cid)
        if status == "COMPLETE" and (not steps or any(s["phase"] != "VERIFICATION" or not s["evidence_id"] for s in steps)):
            problems["COMPLETE: only verification steps, at least one, each citing evidence"].append(cid)
        if status == "INCOMPLETE":
            future_cited = [s for s in steps if s["phase"] != "FINDING" and s["evidence_id"]]
            if not any(s["phase"] == "FINDING" for s in steps) or future_cited:
                problems["INCOMPLETE: has findings; future steps have empty evidence_id"].append(cid)
        for s in steps:
            e = s["evidence_id"]
            if e and (e not in evidence or evidence[e] > audit_at):
                problems["cited ids exist and were available at audit_at"].append(f"{cid}:{e}")
        found = {s["resource_id"] for s in steps if s["phase"] == "FINDING"}
        remediated = {s["resource_id"]: s["operation"] for s in steps if s["phase"] == "REMEDIATION"}
        verified = {(s["resource_id"], s["operation"]) for s in steps if s["phase"] == "VERIFICATION"}
        if found - set(remediated):
            problems["every finding has a remediation on the same resource"].append(cid)
        if any((rid, REMEDIATION_TO_VERIFY[op]) not in verified for rid, op in remediated.items()):
            problems["every remediation has the matching final-state verification"].append(cid)
        if any(rid.startswith(RETAINED_PREFIXES) and op in ("DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD")
               for rid, op in remediated.items()):
            problems["retained categories never deleted/redacted/anonymized"].append(cid)
    return [(name, len(bad), bad[:5]) for name, bad in problems.items()]


def main() -> int:
    pred = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT_PATH
    req = Path(sys.argv[2]) if len(sys.argv) > 2 else DATASET / "deletion_requests.csv"
    results = check(pred, req)
    with open(pred, encoding="utf-8") as f:
        n = sum(1 for _ in csv.DictReader(f))
    print(f"checked {n} rows of {pred.name}")
    for name, count, examples in results:
        print(f"{'PASS' if count == 0 else 'FAIL'}  {name}" + (f"  ({count}: {examples})" if count else ""))
    return 1 if any(c for _, c, _ in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
