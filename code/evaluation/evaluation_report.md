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
| Case-level status accuracy | 32/32 = 100.0% |
| Sequence accuracy (entire ordered steps list identical) | 15/32 = 46.9% |
| Step-level precision | 100.0% (166 predicted steps) |
| Step-level recall | 86.0% (193 gold steps) |
| Step-level F1 | 92.5% |

Field-level accuracy (gold steps aligned to predicted steps by phase + resource_id):

| Field | Accuracy |
|---|---|
| `phase` | 86.0% |
| `system_id` | 86.0% |
| `resource_id` | 86.0% |
| `operation` | 86.0% |
| `state` | 86.0% |
| `evidence_id` | 86.0% |

Custom evaluation cases:

| Case | Result | Detail |
|---|---|---|
| C1 non-deletion intents (EN access, ES correction, FR marketing opt-out) -> NOT_APPLICABLE | PASS | case_901=NOT_APPLICABLE, case_902=NOT_APPLICABLE, case_903=NOT_APPLICABLE |
| C2 historical vs current email for the same person -> identical output | PASS | historical=nadia.ionescu@yahoo.com current=n.petrescu@yahoo.com |
| C3 evidence captured after audit_at is never cited (case_11 re-audited a day earlier) | PASS | status=COMPLETE cited=['evidence_05'] |
| C4 retained records (audit / legal hold / backup) are never deleted, redacted or anonymized | PASS | violations=[] |

Contract and policy checks on the submitted `output.csv` (all 518 rows, `check_output.py`):

| Check | Result |
|---|---|
| header is exactly case_id,deletion_status,steps_json | PASS |
| one row per request, no duplicates or extras | PASS |
| rows sorted by case_id (numeric) | PASS |
| no missing values | PASS |
| deletion_status in allowed set | PASS |
| steps_json is a JSON array of objects with exactly the 7 keys | PASS |
| step_no consecutive from 1 | PASS |
| phase order FINDING > REMEDIATION > VERIFICATION | PASS |
| operation allowed for its phase, state matches operation | PASS |
| within-phase order: operation order, then system_id, resource_id | PASS |
| NOT_APPLICABLE has [] | PASS |
| COMPLETE: only verification steps, at least one, each citing evidence | PASS |
| INCOMPLETE: has findings; future steps have empty evidence_id | PASS |
| cited ids exist and were available at audit_at | PASS |
| every finding has a remediation on the same resource | PASS |
| every remediation has the matching final-state verification | PASS |
| retained categories never deleted/redacted/anonymized | PASS |

Remaining sample mismatches (17):

- case_03: status COMPLETE (gold COMPLETE); missing ['VERIFY_RECORD_ANONYMIZED:analytics_9406']; extra []
- case_04: status COMPLETE (gold COMPLETE); missing ['VERIFY_RECORD_ABSENT:account_004']; extra []
- case_09: status INCOMPLETE (gold INCOMPLETE); missing ['VERIFY_RECORD_ABSENT:account_3960', 'VERIFY_RECORD_PRESENT:audit_record_4902']; extra []
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
| 8 | case_07 predicted COMPLETE, gold INCOMPLETE (the one wrong status). | Its only evidence of `support_001` (a customer's identity/address attachment) is `evidence_58`, a PDF; offline mode skipped all PDFs. | Offline PDF text-layer reader (`extract_pdf`): resource ids become units grounded on the whole document (single-person documents) or on nearby lines only (multi-person registers). 31 → **32 / 32**. Not a case-specific patch: it also reads `audit_record_003` from the security retention extract. |
| 9 | 2 of 518 rows were COMPLETE with no steps at all. | The subject's only linked record (`object_19`, `marketing_001`) had a catalog link but no observed state, so nothing was verified and completion was claimed without evidence. | A data-catalog `LINK_RESOURCE_IDENTITY` entry counts as the weakest evidence that the record exists (ranked below direct observations and listing absence). Linked-but-unobserved records become future checks in INCOMPLETE cases and can never back a COMPLETE verdict. Checker rule "COMPLETE has at least one evidence-backed step" now passes on all 518 rows. |
| 10 | case_07 and case_17 picked up records from a person's earlier deletion (Feb/March), which gold excludes. | Three people filed two requests each (user_07, user_17, user_18); each request covers its own lifecycle. | A record evidenced only up to the audit of an earlier request by the same person belongs to that earlier request, unless it is evidenced again later (e.g. a ghost copy reappearing). Sequence 12 → 14 / 32, precision 95.4% → 98.2%. |
| 11 | case_12 owned a stranger's search entry and, through it, a stranger's connector. | The workflow solver attributed `search_4801` by timing only; a sync event shows it was repopulated by `data_flow_1121`, which the catalog links to user_14. | Sync/restore repopulation events (a) bring their source flow/backup into scope as a recreation path and (b) propagate ownership between source and copy, so the solver excludes the wrong case. Precision **100%**, sequence 15 / 32. |
| 12 | Non-deletion requests competed for deletion-workflow events. | A request that does not authorize deletion never runs a deletion workflow (the activity log records them as classified non-deletion). | Intent is decided for every known request before linking; non-deletion requests are removed from the solver's candidates. No sample change; shrinks candidate sets for the hidden cases. |

## Known remaining errors (honest list)

- **Unresolvable workflow ties (largest gap, all recall).** Some records are linked by nothing except a
  deletion-workflow event, and cases sharing the same `reported_at` produce identical-looking events
  (e.g. case_18 / case_20 each have one primary-profile event inside the other's window). The solver leaves
  ties unlinked rather than guess. This explains the missing `VERIFY_RECORD_ABSENT` on primary accounts and
  `VERIFY_RECORD_ANONYMIZED` on anonymized analytics.
- **Audit records with no visible evidence before `audit_at`.** `audit_record_7095` / `_4128` appear only in
  a snapshot captured after their case's `audit_at`; `audit_record_6645` / `_5569` appear nowhere a
  participant can see. The time cutoff is applied strictly, so these are not reported.
- **Screenshots need Claude.** The 11 PNGs are read only when an API key is available. PDFs are read offline.
- **Catalog vs screenshot conflict.** The catalog links `marketing_001` to user_09, but the marketing console
  screenshot shows a different person. The agent follows the catalog link. I did not add a rule for
  conflicting identities without a labelled example.
- **Recreation-path rule is not validated by the samples.** No sample case contains a sync/restore
  repopulation event, so rule 11(a) is justified by the problem statement, not by a measured gain.

## Optional LLM provider: Gemini free-tier test (measured)

Setup: `gemini-2.5-flash` via `GEMINI_API_KEY` (other flash models listed by the Models API returned
503 "high demand" when probed). Same prompts, JSON schemas and validation as the Claude path.

| Step | Live calls | Result |
|---|---|---|
| Smoke test: 11 PNG screenshots | 11 | 24 records; **0 schema/evidence violations** (id pattern, state enum, timestamp <= capture time). 20 non-platform ids (order numbers, ticket refs, file names) rejected by the validator; 1 file name mapped to `object_19` through a data-catalog alias. Unknown identifiers (staff/business emails, look-alike decoy `g.silva76.jr@gmail.com`) correctly not matched to any user. |
| Smoke test: 19 multilingual intents (10 real non-deletion, 6 deletion in FR/PT/ES/EN, 3 custom) | 1 | 19/19 match expected labels; 19/19 agree with the offline keyword rule. |
| Sample evaluation (32 cases) with Gemini enabled | 8 | Quota hit mid-run: `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, **limit 20 requests/day** (retry after ~15 h). The provider disabled itself and the remaining intent batch fell back to the keyword rule, as designed. |

Total today: 20 live calls, 18,705 input + 59,264 output tokens (output includes thinking), $0 billed (free tier).

**Comparison with the offline baseline (32 samples):** identical. Status 32/32, step precision 100%,
recall 86.0%, F1 92.5%, sequence 15/32 in both runs. The remaining sample errors are linking-recall gaps
(section above) that screenshots and intent do not affect, so **no accuracy gain from Gemini is shown**.
A full 518-request run with Gemini was not performed (daily quota; it was also held back pending a
decision about sending request data to a third-party API). The submitted `output.csv` is the offline,
key-free, reproducible run.
