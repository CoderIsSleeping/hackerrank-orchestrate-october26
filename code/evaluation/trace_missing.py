"""Diagnostic: trace every gold step the agent misses on the public samples back to its evidence.

    LLM_PROVIDER=none python code/evaluation/trace_missing.py

For each missing (case, resource) it prints:
  - evidence units mentioning the resource, split into <= audit_at and after audit_at,
  - whether any visible unit carries the subject's identifiers (grounding),
  - deletion-workflow events naming the resource and the solver's final candidate cases,
  - a classification of why the step is missing.
Gold answers are read only to choose which steps to trace; nothing here feeds the agent.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from config import DATASET  # noqa: E402
from evidence_extract import extract_all  # noqa: E402
from identity import text_tokens  # noqa: E402
from investigator import Investigator  # noqa: E402


def rows(name: str) -> list[dict]:
    with open(DATASET / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    units, scopes, _ = extract_all()
    reqs = {r["case_id"]: r for r in rows("deletion_requests.csv") + rows("sample_deletion_requests.csv")}
    inv = Investigator(units, scopes)
    inv.attach_linker(list(reqs.values()))   # intent exclusions do not affect any sample tie
    lk = inv.linker
    gold = {r["case_id"]: json.loads(r["steps_json"]) for r in rows("sample_output.csv")}
    pred = {r["case_id"]: json.loads(r["steps_json"]) for r in csv.DictReader(open(HERE / "sample_predictions.csv", encoding="utf-8"))}
    key = lambda s: (s["phase"], s["operation"], s["resource_id"], s["evidence_id"])  # noqa: E731
    reasons = Counter()
    for cid, gsteps in gold.items():
        missing = Counter(map(key, gsteps)) - Counter(map(key, pred[cid]))
        if not missing:
            continue
        req, subject = reqs[cid], lk.subjects[cid]
        audit = req["audit_at"]
        for rid in sorted({k[2] for k in missing}):
            ops = sorted({k[1] for k in missing if k[2] == rid})
            us = inv.units_by_resource.get(rid, [])
            before = [u for u in us if u.captured_at <= audit and u.observed_at <= audit]
            after = [u for u in us if u not in before]
            grounded_before = [u.file_id for u in before if text_tokens(u.text) & subject.tokens()]
            grounded_after = [u.file_id for u in after if text_tokens(u.text) & subject.tokens()]
            events = [(aid, e) for aid, e in lk.events_by_id.items() if e["resource_id"] == rid]
            finals = {aid: sorted(lk.final_candidates.get(aid, set())) for aid, _ in events}
            owner = next((c for c, d in lk.assigned.items() if rid in d), None)
            if not us and not events:
                why = "NO_VISIBLE_EVIDENCE: resource appears in no participant file or activity"
            elif not before and grounded_after:
                why = "AFTER_CUTOFF_ONLY: only grounded by evidence captured after audit_at"
            elif not before and not events:
                why = "AFTER_CUTOFF_ONLY: only evidence is after audit_at"
            elif owner and owner != cid:
                why = f"MISATTRIBUTED to {owner}"
            elif events and all(cid in f and len(f) > 1 for f in finals.values()):
                why = "TIE: workflow event still has several candidate cases"
            elif events and not any(cid in f for f in finals.values()):
                why = "OUTSIDE_WINDOW_OR_EXCLUDED: case not among event candidates"
            elif not events and not grounded_before:
                why = "UNGROUNDED_NO_WORKFLOW: no identifier link and no workflow event"
            else:
                why = "OTHER"
            reasons[why.split(":")[0].split(" ")[0]] += len(ops)
            print(f"{cid} {subject.user_id} {rid} {ops}")
            print(f"    units<=audit {sorted({u.file_id for u in before})} grounded {sorted(set(grounded_before))} | "
                  f"after {sorted({u.file_id for u in after})} grounded {sorted(set(grounded_after))}")
            for aid, e in events:
                off = e["event_time"]
                print(f"    event {aid} {off} '{e['details'][:55]}' final candidates {finals[aid]}")
            print(f"    -> {why}")
    print("\nmissing steps by reason:", dict(reasons))


if __name__ == "__main__":
    main()
