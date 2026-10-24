# Ghost Data Still Exists — deletion-audit agent

For every deletion request the agent decides whether the person really asked for deletion. It then
finds every identifier they ever had and every record or process that still exposes them at
`audit_at`. It returns `COMPLETE`, `INCOMPLETE` or `NOT_APPLICABLE` with an ordered, evidence-cited
plan (findings → remediation → verification).

## Setup and run

```bash
python -m pip install -r code/requirements.txt
cp .env.example .env            # then put your ANTHROPIC_API_KEY in .env (never committed)
python code/main.py             # audits dataset/deletion_requests.csv -> output.csv (+ code/evaluation/usage_report.md)
python code/evaluation/main.py  # scores the 32 samples + custom cases -> code/evaluation/evaluation_report.md
```

Python 3.10+. Paths resolve from the repository root, so this works on Windows, macOS and Linux.
Without a key the agent still runs end to end. It falls back to a keyword intent rule and skips
PDFs/screenshots, and the run summary says so. Claude responses are cached in `code/.cache/` by request
hash, so re-runs are deterministic and cost nothing.

## Architecture

```
evidence/ ─► evidence_extract.py   SQL (sqlite) · logs (trace-grouped) · JSON · TXT  ─┐
          └► media.py (Claude)     PDFs + screenshots, validated transcription      ─┴► units + complete listings
request   ─► intent.py (Claude)    multilingual: deletion vs access/export/correction/opt-out
          ─► identity.py           email (current or historical) → user_id → every email/phone/hash
          ─► linker.py             joint attribution of resources to subjects across ALL requests
          ─► investigator.py       state of each resource at audit_at (direct obs > listing absence)
          ─► planner.py            deletion_rules.csv → finding / remediation / verification, ordered
          ─► validator.py          contract checks on every row before output.csv is written
```

**AI for judgement, code for anything that must be exact.** The scoring rewards exact IDs, exact
order and exact evidence, so those are produced by deterministic, explainable code. Claude is used where
a judgement is needed: reading the meaning of a multilingual request, and transcribing facts from images
and PDFs. Its output is schema-constrained and re-validated: resource IDs must match the platform
pattern, states come from an enum, and identifiers are re-matched exactly.

Key decisions (rationale is in the code next to each rule):

- **Global linker.** Deletion-workflow activity names resources but never people, and up to six
  workflows share a timestamp. Events are attributed jointly over all 550 requests: grounded edges
  are fixed, resources grounded elsewhere are excluded, each event template has its own time window,
  each case gets one resource per type and exactly one primary profile, and the result is propagated
  to a fixpoint. Ties stay unlinked rather than guessed.
- **State at `audit_at`.** Only files captured at or before `audit_at` count, for direct observations
  and for absence-from-listing alike. A record seen directly outranks its absence from a bulk listing
  (that is how ghost copies in replicas are caught).
- **Policy from `deletion_rules.csv`.** Categories come from the evidence's own `record_type`, else the
  workflow's attempted action, else the resource prefix. Retained records are verified PRESENT, and
  backups and data flows are blocked or disabled before dependent records are changed.
- **Workflow statuses are claims.** `reported_status` and `reported_result` never decide a state. Only
  observations do.

## Evaluation

`code/evaluation/evaluation_report.md` has case, step, field and sequence accuracy on the samples, four
custom cases, the failures found and the fixes made, and the known remaining errors.
`code/evaluation/usage_report.md` has the model, calls, tokens and cost of the full run.

## Repository notes

- `problem_understanding.md`: problem analysis, traps found in the data, design.
- `log.txt`: the AI-assisted development transcript (AGENTS.md format).
- Package for submission: `Compress-Archive -Path README.md, code -DestinationPath code.zip -Force`
  (remove `code/.cache` and `__pycache__` first; no dataset, no `.env`).
