"""Ghost Data Still Exists - deletion-audit agent.

Usage (from the repository root):
    python code/main.py                      # audit dataset/deletion_requests.csv -> output.csv
    python code/main.py --requests dataset/sample_deletion_requests.csv --out code/evaluation/sample_predictions.csv

Pipeline per run:
  1. Extract every evidence file into normalized units (text formats parsed; PDFs/images read by Claude).
  2. Classify each request's intent (Claude, multilingual; keyword rule as cross-check / fallback).
  3. Link resources to subjects jointly across all requests (identifier grounding + workflow solver).
  4. Reconstruct each resource's state at audit_at, apply deletion_rules.csv, build ordered steps.
  5. Validate every row against the output contract, then write the CSV.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import intent
import llm
import media
from config import DATASET, MODEL, OUTPUT_PATH, PRICE_IN_PER_M, PRICE_OUT_PER_M, REPO_ROOT
from evidence_extract import extract_all, load_manifest
from investigator import Investigator
from planner import build_steps
from validator import validate_row


def read_requests(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def case_num(case_id: str) -> int:
    return int(case_id.rsplit("_", 1)[1])


def run(requests_path: Path, out_path: Path, verbose: bool = True) -> dict:
    started = time.time()
    log = print if verbose else (lambda *a, **k: None)
    manifest = load_manifest()

    media_units, media_notes = media.extract_media(manifest)
    units, scopes, _ = extract_all(media_units)
    log(f"evidence: {len(units)} units, {len(scopes)} complete listings, "
        f"{sum(len(v) for v in media_units.values())} from PDFs/images")

    requests = read_requests(requests_path)
    # Every known request takes part in resource linking: other people's workflows are the decoys
    # each case must be separated from. Their labels are never used.
    all_requests = {r["case_id"]: r for r in read_requests(DATASET / "deletion_requests.csv")}
    all_requests.update({r["case_id"]: r for r in read_requests(DATASET / "sample_deletion_requests.csv")})
    all_requests.update({r["case_id"]: r for r in requests})

    intents, intent_notes = intent.classify([r["request_text"] for r in requests])
    investigator = Investigator(units, scopes)
    investigator.attach_linker(list(all_requests.values()))
    evidence_times = {m["file_id"]: m["captured_at"] for m in manifest}

    rows, errors, trace = [], [], {}
    for req in sorted(requests, key=lambda r: case_num(r["case_id"])):
        verdict = intents[req["request_text"]]
        if not verdict["authorizes_deletion"]:
            status, steps, result = "NOT_APPLICABLE", [], None
        else:
            result = investigator.investigate(req)
            status, steps = build_steps(result.decisions)
        errors += validate_row(req["case_id"], status, steps, req["audit_at"], evidence_times)
        rows.append({"case_id": req["case_id"], "deletion_status": status,
                     "steps_json": json.dumps(steps, ensure_ascii=False, separators=(",", ":"), sort_keys=True)})
        trace[req["case_id"]] = {
            "intent": verdict,
            "user_id": result.subject.user_id if result else None,
            "linked": result.resources if result else {},
            "notes": result.notes if result else [],
        }
    if errors:
        raise SystemExit("contract violations:\n" + "\n".join(errors[:50]))

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["case_id", "deletion_status", "steps_json"])
        w.writeheader()
        w.writerows(rows)

    summary = {
        "requests": len(rows),
        "status_counts": {s: sum(r["deletion_status"] == s for r in rows) for s in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")},
        "seconds": round(time.time() - started, 1),
        "usage": llm.USAGE.as_dict(),
        "llm_available": llm.available(),
        "notes": media_notes + intent_notes,
    }
    log(f"wrote {len(rows)} rows to {out_path.relative_to(REPO_ROOT) if out_path.is_relative_to(REPO_ROOT) else out_path}")
    log(json.dumps({k: summary[k] for k in ("status_counts", "seconds", "usage", "llm_available")}))
    for n in summary["notes"][:20]:
        log("note:", n)
    summary["trace"] = trace
    return summary


def write_usage_report(summary: dict, path: Path) -> None:
    """usage_report.md for the full-dataset run, computed from the client's own counters."""
    usage = summary["usage"]
    n = summary["requests"]
    lines = [
        "# Usage report - final full-dataset run",
        "",
        f"Run: `python code/main.py` on `dataset/deletion_requests.csv` ({n} requests) -> `output.csv`, "
        f"{summary['seconds']} s wall time. Claude available: {summary['llm_available']}.",
        "",
        "Token counts come from `response.usage` of every Claude response the run used (`code/llm.py`).",
        "Responses are cached on disk by request hash, so a re-run replays them: *calls* counts every",
        "response used by the run, *live calls* only those sent during this particular execution.",
        f"Prices: {MODEL} at ${PRICE_IN_PER_M:.2f} / 1M input and ${PRICE_OUT_PER_M:.2f} / 1M output tokens.",
        "",
        "| Provider | Model | Calls | Live calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    tot = {"calls": 0, "live_calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    for model, u in sorted(usage.items()):
        cost = u["input_tokens"] / 1e6 * PRICE_IN_PER_M + u["output_tokens"] / 1e6 * PRICE_OUT_PER_M
        lines.append(f"| Anthropic | `{model}` | {u['calls']} | {u['live_calls']} | {u['input_tokens']:,} | "
                     f"{u['output_tokens']:,} | {u['input_tokens'] + u['output_tokens']:,} | {cost:.4f} |")
        for k in ("calls", "live_calls", "input_tokens", "output_tokens"):
            tot[k] += u[k]
        tot["cost"] += cost
    total_tokens = tot["input_tokens"] + tot["output_tokens"]
    lines += [
        f"| **Overall** | | **{tot['calls']}** | **{tot['live_calls']}** | **{tot['input_tokens']:,}** | "
        f"**{tot['output_tokens']:,}** | **{total_tokens:,}** | **{tot['cost']:.4f}** |",
        "",
        "| Per request (÷ requests) | Value |",
        "|---|---|",
        f"| Model calls | {tot['calls'] / n:.3f} |",
        f"| Tokens | {total_tokens / n:,.1f} |",
        f"| Estimated cost (USD) | {tot['cost'] / n:.5f} |",
        "",
        "Why so few calls: evidence is parsed once per run and shared by all requests; Claude is only used",
        "for judgement that code cannot make - reading the 15 PDFs/screenshots (one call per file) and",
        "classifying the intent of each *unique* request text (batched, 40 per call). Identity resolution,",
        "state reconstruction, policy, ordering and validation are deterministic code with zero tokens.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description="Deletion-audit agent")
    p.add_argument("--requests", type=Path, default=DATASET / "deletion_requests.csv")
    p.add_argument("--out", type=Path, default=OUTPUT_PATH)
    p.add_argument("--run-summary", type=Path, default=None, help="optional JSON file for the run summary")
    args = p.parse_args()
    summary = run(args.requests.resolve(), args.out.resolve())
    if args.requests.resolve() == (DATASET / "deletion_requests.csv").resolve():
        write_usage_report(summary, Path(__file__).resolve().parent / "evaluation" / "usage_report.md")
    if args.run_summary:
        args.run_summary.parent.mkdir(parents=True, exist_ok=True)
        args.run_summary.write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
