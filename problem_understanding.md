# Problem Understanding — Ghost Data Still Exists

Legend: **[V]** = verified in the files or by running code. **[A]** = an assumption, with the reason it was made.

## 1. The problem in my own words

Northstar Shop's deletion workflow reports "Complete" while personal data survives in replicas, search
indexes, support tickets, analytics, exports, logs and backups, and sync or restore jobs can bring it back.
For each of 518 requests the agent must:

1. Decide whether the request actually authorizes deletion. If it does not, the answer is `NOT_APPLICABLE` with `[]`.
2. Resolve every identifier the person has had (current and historical email, phone, email hash, user_id).
3. Find every record and process tied to them, using only evidence available at or before `audit_at`.
4. Establish each record's true state. `reported_status` and `reported_result` are claims, not proof.
5. Apply `deletion_rules.csv`: DELETE, REDACT, ANONYMIZE, RETAIN, or tombstone (REDACT and keep `user_id`).
6. Return `COMPLETE` or `INCOMPLETE` with ordered steps (FINDING → REMEDIATION → VERIFICATION) that cite evidence.

## 2. Requirements (from `problem_statement.md` / `AGENTS.md`) [V]

- `output.csv` at repo root, header `case_id,deletion_status,steps_json`, exactly 518 rows, sorted by case_id.
- Each step has exactly 7 keys. Phases and operations come from fixed lists, and each operation has a fixed state.
- Within a phase, steps follow the table's operation order, then `system_id`, then `resource_id`.
- COMPLETE: verification steps only, each citing a `file_id` / `activity_id` / `audit_id`.
  INCOMPLETE: findings cite evidence; remediation and future verification use `""`.
- Never cite evidence captured after `audit_at`. Never use organizer-only data. Never hardcode labels.
- `code.zip` must include `code/evaluation/evaluation_report.md` and `usage_report.md`. The transcript is the root `log.txt`.

## 3. What the data actually contains [V]

| Item | Finding |
|---|---|
| Requests | 518 to predict; `reported_status` 254 COMPLETE / 152 PARTIALLY_COMPLETE / 112 NOT_STARTED; several languages. **10 do not ask for deletion** (case_533–542: correction, export, access, marketing opt-out). |
| Samples | 32 with answers: 22 INCOMPLETE, 10 COMPLETE, **0 NOT_APPLICABLE**. |
| Identity | 850 users; 306 audit-log changes to email, email_hash, phone. `email_hash` = sha256(email). |
| Activity | 4,011 events, mostly about other people. Deletion-workflow rows name a resource but **never a person**; 550 requests share only **250** distinct `reported_at` values. |
| Evidence | 94 files: 24 SQL snapshots, 26 logs, 29 JSON/TXT, 4 PDFs (all with a text layer), 11 PNG screenshots. Every format reduces to one fact type: *(time, system, resource, category, state, identifiers)*. |
| Repeat requesters | 3 people filed two requests each (user_07, user_17, user_18); each request covers its own lifecycle. |
| Recreation paths | 32 sync/restore events state "…repopulated downstream resource X". |
| Workflow status | A workflow's `reported_status` mirrors its primary-profile operation's result (COMPLETE↔SUCCESS, PARTIALLY_COMPLETE↔PARTIAL, NOT_STARTED↔FAILED). 0 exceptions in 234 label-free attributions and 28 labeled ones. Other workflow events show no such relationship. |

## 4. Traps found (each confirmed on a sample or in the files) [V]

1. `reported_status` is wrong by design (case_11 says COMPLETE; truth is INCOMPLETE).
2. Records may carry only an old email, phone or hash; identifiers vary in case and spacing.
3. Time cutoff is inclusive, and a file's *manifest capture time* matters as well as the snapshot time inside it.
4. Ghost copies: a record seen in a failover replica outranks its absence from a later bulk listing.
5. Over-deletion is wrong: audit logs, legal holds and backups are retained (verified PRESENT / BLOCKED).
6. Support records split: conversations with shared context are REDACTED; attachments are DELETED.
7. Verification covers every relevant record, including ones that are already compliant.
8. Some evidence exists only in PDFs and screenshots.
9. Decoys: other people's workflows run in the same minutes as the subject's.

## 5. Architecture

```
evidence/ ─► evidence_extract.py  SQL (sqlite) · logs (trace-grouped) · JSON · TXT · PDF text layer (offline)
          └► media.py (Claude)    screenshots/PDFs → same unit shape, validated          ─► units + complete listings
request   ─► intent.py            Claude (multilingual) + conservative keyword rule (fallback / cross-check)
          ─► identity.py          email → user_id → every historical identifier
          ─► linker.py            joint attribution of records to people over ALL requests
          ─► investigator.py      state at audit_at · category · evidence to cite
          ─► planner.py           deletion_rules.csv → ordered findings / remediation / verification
          ─► validator.py         per-row contract checks (run stops on any violation)
evaluation/main.py · check_output.py   sample metrics, custom cases, 17 contract/policy checks on output.csv
```

## 6. Design decisions and why

| Decision | Why |
|---|---|
| Claude for judgement only (intent, image transcription); deterministic code for IDs, time, policy, order [V] | The grader rewards exact IDs, order and evidence; code is reproducible and explainable. |
| Global linker instead of per-case time windows [V] | Time windows linked 30 strangers' records to one case. The solver fixes grounded edges, excludes records grounded to others, uses per-template time windows, one resource per type, one primary profile per case, and **leaves ties unlinked**. |
| Evidence strength: direct observation > absence from a complete listing > catalog link [V] | Catches ghost copies; avoids claiming COMPLETE without proof. |
| Category: evidence `record_type` > the workflow's attempted action > resource prefix [V] | Fixes analytics records the workflow deletes rather than anonymizes, and the support split. |
| Status consistency as a *linking* constraint for primary-profile events only [V] | Resolves ties that timing cannot (recall 86.0% → 90.2%, precision 100%). Never used as proof of a record's state. |
| Snapshot propagation rounds in the solver [V] | Found by a row-shuffle regression test: the result had depended on input order. |
| Earlier-lifecycle scoping for repeat requesters [V] | Removed extra steps on case_07 / case_17 without per-case code. |
| Sync/restore repopulation → recreation path and shared ownership [A] | Required by the problem statement; no sample contains such an event, so the gain is unmeasured. |
| Strict time cutoff, even for identity links [A] | AGENTS.md: later evidence "must not change the answer". Costs some recall (audit records visible only later). |
| Offline-first; Claude optional, with every failure falling back [V] | Tested with an invalid key: Claude is disabled, the run finishes, and the output is byte-identical to offline. |

## 7. Evaluation (32 samples, offline run) [V]

Status **32/32**. Step precision **100%**, recall **90.2%**, F1 **94.8%**. Exact sequence **18/32**.
Custom cases C1–C4 and 15 regression tests pass, and all 17 contract checks pass on the 518-row `output.csv`.
Of the 19 remaining missing steps, 11 are ties no grounded constraint separates, 5 have evidence only
after `audit_at`, and 3 have no participant-visible evidence.
Details and the failure → fix history are in `code/evaluation/evaluation_report.md`.

## 8. Remaining risks

- **Recall on workflow-only records** when cases share a timestamp *and* a primary-operation result, and
  for anonymized analytics. The solver refuses to guess, so these verification steps are missing. [V]
- The status-consistency constraint holds without exception on this dataset. If the hidden labels came from
  a generator that does not follow it, some primary-account steps could be misattributed. [A]
- **Screenshots** are only read with an API key. Hidden cases whose only evidence is a PNG fall back to
  catalog links or nothing. [V]
- **Hidden-set distribution** may differ from the 32 samples (e.g. more NOT_APPLICABLE or restore-path cases). [A]
- **Identity conflicts** (catalog says person A, screenshot shows person B) follow the catalog. [A]
- Sample answers show **one evidence file per case** in many cases. The citation rule (prefer the file
  that observes most of the case's records) reproduces this on the samples but is inferred. [A]
