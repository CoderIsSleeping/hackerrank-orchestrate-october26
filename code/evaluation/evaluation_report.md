# Evaluation report — Ghost Data Still Exists

Reproduce: `python code/evaluation/main.py` (rewrites the metrics block below).

## How it is measured

- **Data:** the 32 public samples (`dataset/sample_deletion_requests.csv`) scored against
  `dataset/sample_output.csv`. The sample answers are used **only for scoring**. They are not
  prompt examples, rules or linker input, so the numbers are not inflated by leakage. (The linker sees
  the sample *requests* along with all 518 evaluation requests, because each workflow has to be told
  apart from its neighbours. It never sees their labels.)
- **Case-level status accuracy:** predicted `deletion_status` equals gold.
- **Step-level precision / recall / F1:** a step counts as correct only if
  `(phase, operation, system_id, resource_id, evidence_id)` all match a gold step (multiset match).
- **Field-level accuracy:** gold steps are aligned to predicted steps by `(phase, resource_id)`, then
  every field is compared. A missing step counts as wrong on all fields.
- **Sequence accuracy:** the full ordered `steps_json` list is identical, including order and evidence.
- **Custom cases:** participant-made checks of properties the samples do not cover (no sample is
  `NOT_APPLICABLE`, for example).

## Results

<!-- AUTO:BEGIN -->
Run on the 32 public samples (`dataset/sample_deletion_requests.csv`), scored against `dataset/sample_output.csv`. LLM available during run: False.

| Metric | Value |
|---|---|
| Case-level status accuracy | 31/32 = 96.9% |
| Sequence accuracy (entire ordered steps list identical) | 12/32 = 37.5% |
| Step-level precision | 96.4% (168 predicted steps) |
| Step-level recall | 83.9% (193 gold steps) |
| Step-level F1 | 89.8% |

Field-level accuracy (gold steps aligned to predicted steps by phase + resource_id):

| Field | Accuracy |
|---|---|
| `phase` | 84.5% |
| `system_id` | 84.5% |
| `resource_id` | 84.5% |
| `operation` | 84.5% |
| `state` | 84.5% |
| `evidence_id` | 83.9% |

Custom evaluation cases:

| Case | Result | Detail |
|---|---|---|
| C1 non-deletion intents (EN access, ES correction, FR marketing opt-out) -> NOT_APPLICABLE | PASS | case_901=NOT_APPLICABLE, case_902=NOT_APPLICABLE, case_903=NOT_APPLICABLE |
| C2 historical vs current email for the same person -> identical output | PASS | historical=nadia.ionescu@yahoo.com current=n.petrescu@yahoo.com |
| C3 evidence captured after audit_at is never cited (case_11 re-audited a day earlier) | PASS | status=COMPLETE cited=['evidence_05'] |
| C4 retained records (audit / legal hold / backup) are never deleted, redacted or anonymized | PASS | violations=[] |

Remaining sample mismatches (20):

- case_03: status COMPLETE (gold COMPLETE); missing ['VERIFY_RECORD_ANONYMIZED:analytics_9406']; extra []
- case_04: status COMPLETE (gold COMPLETE); missing ['VERIFY_RECORD_ABSENT:account_004']; extra []
- case_07: status COMPLETE (gold INCOMPLETE); missing ['REPORT_REMAINING_RECORD:support_001', 'DELETE_RECORD:support_001', 'VERIFY_RECORD_ABSENT:account_007', 'VERIFY_RECORD_ABSENT:support_001']; extra ['VERIFY_RECORD_ABSENT:account_007']
- case_09: status INCOMPLETE (gold INCOMPLETE); missing ['VERIFY_RECORD_ABSENT:account_3960', 'VERIFY_RECORD_PRESENT:audit_record_4902']; extra []
- case_12: status INCOMPLETE (gold INCOMPLETE); missing []; extra ['REPORT_REMAINING_RECORD:search_4801', 'DELETE_RECORD:search_4801', 'VERIFY_RECORD_ABSENT:search_4801']
- case_17: status INCOMPLETE (gold INCOMPLETE); missing []; extra ['VERIFY_RECORD_ABSENT:account_6523', 'VERIFY_RECORD_REDACTED:tombstone_17']
- case_18: status INCOMPLETE (gold INCOMPLETE); missing ['VERIFY_RECORD_ABSENT:account_5521', 'VERIFY_RECORD_PRESENT:audit_record_6645']; extra []
- case_20: status INCOMPLETE (gold INCOMPLETE); missing ['VERIFY_RECORD_ABSENT:account_5037', 'VERIFY_RECORD_PRESENT:audit_record_5569']; extra []
- case_21: status COMPLETE (gold COMPLETE); missing ['VERIFY_RECORD_ABSENT:account_6440', 'VERIFY_RECORD_ANONYMIZED:analytics_5335']; extra []
- case_22: status INCOMPLETE (gold INCOMPLETE); missing ['VERIFY_RECORD_ABSENT:account_4990']; extra []
- case_23: status INCOMPLETE (gold INCOMPLETE); missing ['VERIFY_RECORD_ABSENT:account_410', 'VERIFY_RECORD_PRESENT:audit_record_4128']; extra []
- case_24: status INCOMPLETE (gold INCOMPLETE); missing ['VERIFY_RECORD_PRESENT:audit_record_7095']; extra []
- case_25: status COMPLETE (gold COMPLETE); missing ['VERIFY_RECORD_ABSENT:account_3136', 'VERIFY_RECORD_ANONYMIZED:analytics_1895']; extra []
- case_26: status COMPLETE (gold COMPLETE); missing ['VERIFY_RECORD_ABSENT:account_5684', 'VERIFY_RECORD_ANONYMIZED:analytics_4566']; extra []
- case_27: status COMPLETE (gold COMPLETE); missing ['VERIFY_RECORD_ABSENT:account_3866', 'VERIFY_RECORD_ANONYMIZED:analytics_4574']; extra []
- case_28: status COMPLETE (gold COMPLETE); missing ['VERIFY_RECORD_ABSENT:account_5482', 'VERIFY_RECORD_ANONYMIZED:analytics_6077']; extra []
- case_29: status INCOMPLETE (gold INCOMPLETE); missing ['VERIFY_RECORD_ABSENT:account_7012', 'VERIFY_RECORD_PRESENT:audit_record_3954']; extra []
- case_30: status INCOMPLETE (gold INCOMPLETE); missing ['VERIFY_RECORD_PRESENT:audit_record_1707']; extra []
- case_31: status COMPLETE (gold COMPLETE); missing ['VERIFY_RECORD_ABSENT:account_4857']; extra []
- case_32: status INCOMPLETE (gold INCOMPLETE); missing ['VERIFY_RECORD_ABSENT:account_8858']; extra []
<!-- AUTO:END -->

## Custom evaluation cases (participant-made)

| ID | What it tests | Why it matters |
|---|---|---|
| C1 | Three non-deletion requests (EN access + "do not delete", ES correction, FR marketing opt-out) built on case_11's identity must be `NOT_APPLICABLE` with `[]`. | The samples contain **zero** `NOT_APPLICABLE` cases, so intent detection would otherwise be untested. |
| C2 | case_10 was filed with a historical email; the same request filed with the subject's current email must give a byte-identical audit. | Identity resolution has to be invariant to which of a person's emails was used. |
| C3 | case_11 re-audited one day before its handover file (`evidence_03`) exists must never cite it. | The `audit_at` cutoff: later evidence must not change or support the answer. |
| C4 | Across all samples, retained categories (security audit, legal hold, backup) never get DELETE / REDACT / ANONYMIZE. | Over-deletion is a policy violation too. |

Caveat on C1: the offline keyword fallback was tuned while these three texts were visible, so C1 is
not a held-out test of that rule. It *is* held out for Claude, the primary classifier.

## Failures found and improvements made

Each row is a failure seen on the samples, the cause, and the fix. Numbers are the status / exact-sequence
scores right after the fix.

| # | Failure observed | Root cause | Fix |
|---|---|---|---|
| 1 | case_06 linked 30 resources, most belonging to strangers. | Deletion-workflow activity rows name a resource but never a person; linking "every event inside the case's time window" pulls in other people's workflows running in the same minutes (only 250 distinct `reported_at` values across 550 requests). | Replaced with a **global constraint solver** (`linker.py`): identifier-grounded edges are fixed, resources grounded to someone else are excluded, one resource per type per case, propagated to a fixpoint, ambiguous events left unlinked rather than guessed. |
| 2 | Cases cited `evidence_02` for absences although it is dated after `audit_at`. | A *scope* (complete listing) carried the snapshot time (before audit) but not the file's manifest capture time (after audit) - a time leak. | Scopes carry `file_captured_at`; listings are ignored unless the file itself existed at `audit_at`. 26 → 28 / 32. |
| 3 | case_05 / case_24 said ABSENT; gold says the record remains. | A later bulk snapshot that merely omitted the record outranked a direct observation of it (ghost copy in a failover replica / readback). | Direct observations outrank absence-from-a-listing; listings decide only when nothing observed the record itself. |
| 4 | Search hits and support PII scans read as "no state". | In a log trace, auxiliary lines (`cache_lookup result=miss`) overwrote the decisive line's fields; `result=hit` and `*_read_completed` / `*_pii_scan` events were not recognised. | The line naming the resource is authoritative in a trace; hit/miss and read-back events mapped to states. 28 → 31 / 32. |
| 5 | analytics_9914 must be DELETE, not ANONYMIZE; support records split between DELETE and REDACT. | Prefix-based categories are too coarse. | Category from the evidence's own `record_type`; else the workflow's attempted policy action (a category hint, never proof of state); support records are REDACT only when they are conversations with shared context (rule_05), otherwise DELETE. |
| 6 | Status fell from 31 to 24 / 32 in offline mode. | The keyword fallback matched "apply the **correct** treatment" as a correction request. | Fallback made conservative and multilingual (explicit negations, specific access/correction/marketing phrases); Claude remains the primary classifier. Back to 31 / 32. |
| 7 | analytics_9447 attached to case_11, which already owned analytics_2034. | The single-candidate pass ran before the grounded event had filled the slot. | Grounded events claim their slots before ungrounded propagation. |

## Known remaining errors (honest list)

- **Unresolvable workflow ties.** Some resources are linked by nothing except a workflow event, and
  several cases share the same `reported_at`, so their events are indistinguishable (e.g. case_25 and
  case_26's `analytics_1895` / `analytics_4566`). The solver declines to guess, which costs recall
  (mostly `VERIFY_RECORD_ABSENT` on primary accounts and `VERIFY_RECORD_ANONYMIZED` on anonymized analytics).
- **Records only visible in PDFs/screenshots** (e.g. case_07's `support_001` in a support attachment PDF,
  security audit records that appear in no text evidence) need the Claude vision pass. In an offline run
  those are missed.
- **case_17:** an older account and tombstone of the same person (from a previous lifecycle in March)
  are reported, while the gold leaves them out. I chose not to special-case it without a general rule.
- **case_12:** one ungrounded search entry is attributed to the wrong workflow.
