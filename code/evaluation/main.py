"""Evaluation workflow.

    python code/evaluation/main.py

1. Runs the agent on dataset/sample_deletion_requests.csv and scores it against sample_output.csv:
   - case-level status accuracy
   - step-level precision / recall / F1 (a step = phase, operation, system, resource, evidence)
   - field-level accuracy on matched steps, per field
   - sequence accuracy (the whole ordered steps list identical)
2. Runs participant-made custom cases (intent, identity, time cutoff, retention safety).
3. Rewrites the metrics block of evaluation_report.md between the AUTO markers.

Sample answers are used only here, for scoring. They never feed prompts, rules or the linker.
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from config import DATASET  # noqa: E402
from main import run  # noqa: E402

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
REPORT = HERE / "evaluation_report.md"


def load(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        return {r["case_id"]: r for r in csv.DictReader(f)}


def step_key(s: dict) -> tuple:
    return tuple(s[f] for f in FIELDS)


def score(pred: dict[str, dict], gold: dict[str, dict]) -> tuple[dict, list[str]]:
    status_ok = seq_ok = tp = fp = fn = 0
    field_hits, field_total = Counter(), 0
    failures = []
    for cid, g in gold.items():
        p = pred[cid]
        gs, ps = json.loads(g["steps_json"]), json.loads(p["steps_json"])
        status_ok += p["deletion_status"] == g["deletion_status"]
        seq_ok += [step_key(s) for s in ps] == [step_key(s) for s in gs]
        G, P = Counter(map(step_key, gs)), Counter(map(step_key, ps))
        tp += sum((G & P).values())
        fp += sum((P - G).values())
        fn += sum((G - P).values())
        # Field-level: align steps by (phase, resource_id), then compare every field.
        p_by = {(s["phase"], s["resource_id"]): s for s in ps}
        for s in gs:
            field_total += 1
            m = p_by.get((s["phase"], s["resource_id"]))
            for f in FIELDS:
                field_hits[f] += bool(m) and m[f] == s[f]
        if p["deletion_status"] != g["deletion_status"] or G != P:
            missing = [f"{k[3]}:{k[2]}" for k in (G - P)]
            extra = [f"{k[3]}:{k[2]}" for k in (P - G)]
            failures.append(f"{cid}: status {p['deletion_status']} (gold {g['deletion_status']}); "
                            f"missing {missing[:4]}{'…' if len(missing) > 4 else ''}; extra {extra[:4]}{'…' if len(extra) > 4 else ''}")
    n = len(gold)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    metrics = {
        "cases": n,
        "status_accuracy": status_ok / n,
        "status_correct": status_ok,
        "sequence_accuracy": seq_ok / n,
        "sequence_correct": seq_ok,
        "step_precision": prec,
        "step_recall": rec,
        "step_f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
        "steps_gold": tp + fn,
        "steps_pred": tp + fp,
        "field_accuracy": {f: field_hits[f] / field_total for f in FIELDS},
    }
    return metrics, failures


# ------------------------------------------------------------------ custom cases

def custom_cases(tmp: Path) -> list[tuple[str, bool, str]]:
    """Participant-made cases. Each builds a request file, runs the agent, and checks one property."""
    samples = list(csv.DictReader(open(DATASET / "sample_deletion_requests.csv", encoding="utf-8")))
    base = {r["case_id"]: r for r in samples}
    results = []

    def run_rows(rows: list[dict]) -> dict[str, dict]:
        req = tmp / "custom_requests.csv"
        out = tmp / "custom_out.csv"
        with open(req, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(samples[0].keys()))
            w.writeheader()
            w.writerows(rows)
        run(req, out, verbose=False)
        return load(out)

    # C1 - non-deletion requests in three languages must be NOT_APPLICABLE with [].
    texts = [
        "Please send me a copy of all the personal data you hold about me. Do not delete my account.",
        "Quiero corregir la dirección de envío de mi cuenta; no deseo eliminarla.",
        "Merci de ne plus m'envoyer de newsletters marketing, mais gardez mon compte actif.",
    ]
    rows = [{**base["case_11"], "case_id": f"case_90{i}", "request_text": t} for i, t in enumerate(texts, 1)]
    out = run_rows(rows)
    ok = all(out[r["case_id"]]["deletion_status"] == "NOT_APPLICABLE" and out[r["case_id"]]["steps_json"] == "[]" for r in rows)
    results.append(("C1 non-deletion intents (EN access, ES correction, FR marketing opt-out) -> NOT_APPLICABLE", ok,
                    ", ".join(f"{r['case_id']}={out[r['case_id']]['deletion_status']}" for r in rows)))

    # C2 - identity invariance: case_10 was filed with a historical email; the same request filed
    # with the subject's current email must produce the identical audit.
    from identity import IdentityIndex
    idx = IdentityIndex()
    subject, _ = idx.resolve(base["case_10"]["email"])
    current = idx.users[subject.user_id]["email"]
    rows = [{**base["case_10"], "case_id": "case_911"}, {**base["case_10"], "case_id": "case_912", "email": current}]
    out = run_rows(rows)
    ok = out["case_911"]["steps_json"] == out["case_912"]["steps_json"] and current != base["case_10"]["email"]
    results.append(("C2 historical vs current email for the same person -> identical output", ok,
                    f"historical={base['case_10']['email']} current={current}"))

    # C3 - time cutoff: case_11 audited one day before its handover evidence (evidence_03) exists must
    # not cite that file.
    rows = [{**base["case_11"], "case_id": "case_921", "audit_at": "2026-09-30T12:00:00Z"}]
    out = run_rows(rows)
    cited = {s["evidence_id"] for s in json.loads(out["case_921"]["steps_json"])}
    ok = "evidence_03" not in cited
    results.append(("C3 evidence captured after audit_at is never cited (case_11 re-audited a day earlier)", ok,
                    f"status={out['case_921']['deletion_status']} cited={sorted(c for c in cited if c)}"))

    # C4 - retention safety: across every sample case, a retained category (security audit, legal hold,
    # backup archive) never receives DELETE / REDACT / ANONYMIZE.
    out = run_rows([{**r} for r in samples])
    bad = [(cid, s["resource_id"]) for cid, r in out.items() for s in json.loads(r["steps_json"])
           if s["resource_id"].startswith(("audit_record_", "legal_hold_", "backup_"))
           and s["operation"] in ("DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD")]
    results.append(("C4 retained records (audit / legal hold / backup) are never deleted, redacted or anonymized",
                    not bad, f"violations={bad[:5]}"))
    return results


# ------------------------------------------------------------------ report

def render(metrics: dict, failures: list[str], custom: list[tuple[str, bool, str]], summary: dict,
           checks: list[tuple[str, int, list]] | None = None) -> str:
    fa = metrics["field_accuracy"]
    lines = [
        f"Run on the {metrics['cases']} public samples (`dataset/sample_deletion_requests.csv`), "
        f"scored against `dataset/sample_output.csv`. LLM available during run: {summary['llm_available']}.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Case-level status accuracy | {metrics['status_correct']}/{metrics['cases']} = {metrics['status_accuracy']:.1%} |",
        f"| Sequence accuracy (entire ordered steps list identical) | {metrics['sequence_correct']}/{metrics['cases']} = {metrics['sequence_accuracy']:.1%} |",
        f"| Step-level precision | {metrics['step_precision']:.1%} ({metrics['steps_pred']} predicted steps) |",
        f"| Step-level recall | {metrics['step_recall']:.1%} ({metrics['steps_gold']} gold steps) |",
        f"| Step-level F1 | {metrics['step_f1']:.1%} |",
        "",
        "Field-level accuracy (gold steps aligned to predicted steps by phase + resource_id):",
        "",
        "| Field | Accuracy |",
        "|---|---|",
        *[f"| `{f}` | {v:.1%} |" for f, v in fa.items()],
        "",
        "Custom evaluation cases:",
        "",
        "| Case | Result | Detail |",
        "|---|---|---|",
        *[f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |" for name, ok, detail in custom],
        "",
        "Contract and policy checks on the submitted `output.csv` (all 518 rows, `check_output.py`):",
        "",
        "| Check | Result |",
        "|---|---|",
        *[f"| {name} | {'PASS' if n == 0 else f'FAIL ({n})'} |" for name, n, _ in (checks or [])],
        "",
        f"Remaining sample mismatches ({len(failures)}):",
        "",
        *[f"- {f}" for f in failures],
    ]
    return "\n".join(lines)


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        pred_path = HERE / "sample_predictions.csv"
        summary = run(DATASET / "sample_deletion_requests.csv", pred_path, verbose=False)
        metrics, failures = score(load(pred_path), load(DATASET / "sample_output.csv"))
        custom = custom_cases(tmp)
    from config import OUTPUT_PATH
    from check_output import check
    checks = check(OUTPUT_PATH, DATASET / "deletion_requests.csv") if OUTPUT_PATH.exists() else []
    block = render(metrics, failures, custom, summary, checks)
    print(block)
    text = REPORT.read_text(encoding="utf-8") if REPORT.exists() else "<!-- AUTO:BEGIN -->\n<!-- AUTO:END -->\n"
    head, _, rest = text.partition("<!-- AUTO:BEGIN -->")
    _, _, tail = rest.partition("<!-- AUTO:END -->")
    REPORT.write_text(f"{head}<!-- AUTO:BEGIN -->\n{block}\n<!-- AUTO:END -->{tail}", encoding="utf-8")


if __name__ == "__main__":
    main()
