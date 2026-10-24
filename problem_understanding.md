# Problem Understanding — Ghost Data Still Exists

## 1. The problem in my own words

Northstar Shop's deletion workflow reports **"Complete"**, but a customer's personal data keeps living on in
replicas, search indexes, support tickets, analytics, exports, logs and backups. Worse, sync jobs and backup
restores can **bring deleted data back**.

I am building a **deletion-audit agent**. For each of the 518 requests it must:

1. **Decide whether the request really asks for deletion.** If not, the answer is `NOT_APPLICABLE` with `[]`.
2. **Find every identifier the person has ever had**: current email, phone and email hash, plus old values from `user_audit_log.csv`.
3. **Find every record about them** across all evidence formats, using only evidence captured **at or before `audit_at`**.
4. **Work out the true state of each record** at `audit_at`. `reported_status` and `reported_result` are *claims*, not proof.
5. **Apply `deletion_rules.csv`** to each record: DELETE, REDACT, ANONYMIZE, RETAIN, or tombstone (REDACT, keep `user_id`).
6. **Decide `COMPLETE` vs `INCOMPLETE`**, then output an **ordered** plan:
   `FINDING → REMEDIATION → VERIFICATION`, citing evidence IDs.

## 2. What the data actually looks like (checked, not assumed)

| File | Size | What I learned |
|---|---|---|
| `deletion_requests.csv` | 518 cases | `reported_status`: 254 COMPLETE, 152 PARTIALLY_COMPLETE, 112 NOT_STARTED. Requests come in several languages (EN, FR, PT, ES, JA, …). |
| `sample_output.csv` | 32 cases | **22 INCOMPLETE, 10 COMPLETE, 0 NOT_APPLICABLE.** |
| `user_identities.csv` | 850 users | Current name, email, phone, email_hash. |
| `user_audit_log.csv` | 306 changes | Changes to `email`, `email_hash` and `phone`. Old values are how "ghost" records are found. |
| `system_activity.csv` | 4,011 events | 17 event types (DELETE, SYNC, INDEX, RESTORE_BACKUP, DISABLE_DATA_FLOW, …). **721 FAILED, 376 PARTIAL.** Global stream, mostly about other people. |
| `deletion_rules.csv` | 15 rules | One rule per data category (see §4). |
| `evidence_files.csv` | 94 files | 24 SQL snapshots, 26 logs, 29 JSON/TXT records, 4 PDFs, 11 PNG screenshots. |

**Key observation:** every evidence format eventually states the same fact:
`(captured_at, system_id, resource_id, data_category, observed_state, identifier)`.
For example, the handover TXT (`evidence_03`) has lines like
`account_service | account_4248 | USER_PROFILE | NOT_FOUND | novak.tomasz@icloud.com`.
SQL replicas, logs, JSON and screenshots state the same thing in different formats.

## 3. Traps and missing pieces I found

1. **Reported status is a lie by design.** Sample `case_11` reports `COMPLETE`, but the truth is `INCOMPLETE`:
   a search entry, analytics and support records are still identifiable, and a data flow is still active.
2. **Historical identifiers.** Records may only mention an *old* email, phone or hash. Searching only the current email misses them.
3. **Identifier formatting differs.** For example `CHOI.JIHOON94@GMAIL.COM` vs lowercase, and phone numbers with or without spaces.
   Everything must be normalized before matching.
4. **Time cutoff.** Evidence captured after `audit_at` must be ignored. The boundary is inclusive:
   `evidence_03` is captured exactly at `case_11`'s `audit_at` and is cited.
5. **Latest state wins.** Older snapshots can show a record present and newer ones absent, or the reverse when a sync recreates it.
   The decision uses the **latest evidence ≤ audit_at per resource**.
6. **Replication drift.** The primary DB can be clean while a read, reporting or failover replica still holds the row.
7. **Recreation paths.** An ACTIVE data flow or an unblocked backup restore makes a case INCOMPLETE even if all records are gone.
8. **Over-deleting is also wrong.** `SECURITY_AUDIT_LOG`, `LEGAL_HOLD_RECORD` and `BACKUP_ARCHIVE` must be **retained**, and verified PRESENT.
   The account tombstone is **REDACTED, not deleted**.
9. **Verification covers everything relevant**, not just what was fixed. In `case_11` the plan also verifies the already-deleted
   account (ABSENT) and the retained audit record (PRESENT).
10. **Multimodal evidence.** 4 PDFs and 11 screenshots hold facts that text search can't see. They need OCR or a vision model.
11. **No NOT_APPLICABLE example in the samples.** So the intent classifier can't be validated on the given samples.
    I must build my own test cases (which also satisfies the "≥3 custom evaluation cases" requirement).
12. **Decoys.** Global files contain unrelated people and look-alike identifiers. A match has to be grounded in an exact normalized identifier
    or a resource ID linked to the subject.

## 4. Policy table (from `deletion_rules.csv`)

| Category | Action | Remediation op | Verification op |
|---|---|---|---|
| USER_PROFILE, SECONDARY_IDENTIFIER, SEARCH_INDEX_ENTRY, CACHE_ENTRY, USER_EXPORT, MARKETING_PROFILE | DELETE | `DELETE_RECORD` | `VERIFY_RECORD_ABSENT` |
| SUPPORT_ATTACHMENT (subject-only) | DELETE | `DELETE_RECORD` | `VERIFY_RECORD_ABSENT` |
| SUPPORT_MESSAGE (shared context), EMAIL_DELIVERY_LOG (has email), ACCOUNT_TOMBSTONE | REDACT | `REDACT_RECORD` | `VERIFY_RECORD_REDACTED` |
| ANALYTICS_PROFILE, PRODUCT_TELEMETRY (attributable) | ANONYMIZE | `ANONYMIZE_RECORD` | `VERIFY_RECORD_ANONYMIZED` |
| SECURITY_AUDIT_LOG, LEGAL_HOLD_RECORD, BACKUP_ARCHIVE | RETAIN | none (BLOCK_RESTORE for a backup) | `VERIFY_RECORD_PRESENT` |
| Active data flow | — | `DISABLE_DATA_FLOW` | `VERIFY_DATA_FLOW_DISABLED` |
| Restore risk | — | `BLOCK_RESTORE` | `VERIFY_RESTORE_BLOCKED` |

## 5. How we are solving it — architecture

The design principle: **the LLM handles judgement, and code handles anything that must be exact.**
The scoring rewards exact IDs, exact order and exact evidence. Those must be deterministic and explainable.

```
            ┌───────────────────────── one-time, cached ─────────────────────────┐
evidence/ → │ Extractors: SQL(sqlite) · logs · JSON/TXT · PDF text · PNG (VLM/OCR) │ → observations table
            └────────────────────────────────────────────────────────────────────┘
request ─→ 1. Intent classifier (LLM, multilingual, + rule check)  → NOT_APPLICABLE? → []
        ─→ 2. Identity resolver (current + audit history, normalized)  → identifier set
        ─→ 3. Evidence matcher (identifiers → observations, ≤ audit_at)  → candidate records
        ─→ 4. State reconstructor (latest observation per resource; activity as claims)
        ─→ 5. Policy engine (deletion_rules → required treatment per record)
        ─→ 6. Planner (findings → remediation → verification, contract ordering)
        ─→ 7. Validator (schema, ops/states allowed, step_no, evidence ≤ audit_at)  → output.csv
```

Where the AI makes **bounded decisions**:
- **Intent** of a multilingual request (deletion vs access/export/correction/opt-out). The model returns a label and a short rationale,
  validated against an enum.
- **Multimodal extraction** from PDFs and screenshots into the same observation schema. The output is validated: every resource_id or
  identifier must actually look like one.
- **Ambiguous evidence**: when two sources disagree or a category is unclear, the model is asked with only that case's
  evidence, and must cite the evidence ID it relied on.

Hard rules stay in code and **cannot be overridden by the model**: the time cutoff, the allowed operations and states, the ordering, retain-not-delete,
and that evidence for COMPLETE verification must exist and be ≤ audit_at.

## 6. Evaluation plan (honest by design)

- `code/evaluation/main.py` compares our output on the 32 samples with `sample_output.csv`:
  **case status accuracy, step-level exact match, field-level accuracy, sequence accuracy**.
- **No sample answers are used as prompt examples or rules.** The samples are only used to score, so the metric isn't leaked.
- **≥3 custom cases** built by me: a NOT_APPLICABLE request (export or access), a historical-email-only match,
  evidence after `audit_at` that must be ignored, a retained record that must not be deleted.
- A failure log in `evaluation_report.md`: what broke, why, and what I changed.
- `usage_report.md`: provider, model, calls, input/output tokens, cost total and per request, all from real counters in the code.

## 7. What it takes to stand out

1. **A transcript that shows my thinking.** Every prompt logged word for word to `log.txt` (plan, build, debug, verify).
2. **An evidence-grounded agent.** Every finding cites the exact file, activity or audit ID, and nothing is padded.
3. **Deterministic and reproducible.** Same input gives the same output. LLM responses are cached, and JSON serialization is stable.
4. **Token-efficient.** Evidence is extracted once and cached, the LLM is called only where judgement is needed, and the cost is reported.
5. **Honest metrics.** "X/32 on samples" plus the failure list. Never "perfect".
6. **Interview-ready.** Every constant and rule explained: input → rule → file:line → why → what would change it.

## 8. Required deliverables checklist

- [ ] `output.csv` at repo root: header `case_id,deletion_status,steps_json`, **518 rows**, sorted by `case_id`.
- [ ] Valid JSON arrays, `step_no` from 1 with no gaps, phase order respected, only allowed ops and states.
- [ ] COMPLETE: verification steps cite evidence. INCOMPLETE: remediation and verification have `evidence_id: ""`.
- [ ] `code/` runnable from the terminal: `python code/main.py`.
- [ ] `README.md` with setup and run steps.
- [ ] `code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md`.
- [ ] `code.zip` with no dataset, venv, caches, nested zips or secrets.
- [ ] `log.txt` (chat transcript) with every turn logged, each with `tool=` and `model=`.
- [ ] `agent-trace.zip` (Claude Code session JSONL + `/export`).
