"""Small, bounded live test of the configured LLM provider before any full-dataset run.

    python code/evaluation/llm_smoke_test.py          # uses whichever provider llm.py selects

1. Screenshots: transcribes the 11 PNG evidence files (one call each) and validates every record:
   resource id pattern, state enum, timestamp format and <= file capture time, identifier formats,
   and whether emails / user_ids exist in the identity data (unknown ones are reported, not dropped).
2. Intent: classifies a fixed, small multilingual set (one batched call) and compares with the
   offline keyword rule and with the expected label written next to each text.
Prints calls, tokens, errors and quota events. Never prints keys.
"""
from __future__ import annotations

import csv
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import intent  # noqa: E402
import llm  # noqa: E402
import media  # noqa: E402
from config import DATASET  # noqa: E402
from evidence_extract import RESOURCE_RE, load_manifest  # noqa: E402
from identity import EMAIL_RE, HASH_RE, PHONE_RE, IdentityIndex, norm_email  # noqa: E402

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")


def intent_set() -> list[tuple[str, bool]]:
    """(text, expected authorizes_deletion). Non-deletion texts are the 10 such requests in the data."""
    reqs = {r["case_id"]: r["request_text"] for r in csv.DictReader(open(DATASET / "deletion_requests.csv", encoding="utf-8"))}
    samples = {r["case_id"]: r["request_text"] for r in csv.DictReader(open(DATASET / "sample_deletion_requests.csv", encoding="utf-8"))}
    out = [(reqs[f"case_{n}"], False) for n in range(533, 543)]
    out += [(reqs["case_84"], True), (reqs["case_522"], True), (samples["case_02"], True),
            (samples["case_26"], True), (samples["case_15"], True), (reqs["case_33"], True)]
    out += [("Please send me a copy of all the personal data you hold about me. Do not delete my account.", False),
            ("Quiero corregir la dirección de envío de mi cuenta; no deseo eliminarla.", False),
            ("Merci de ne plus m'envoyer de newsletters marketing, mais gardez mon compte actif.", False)]
    return out


def main() -> int:
    if not llm.available():
        print("No LLM provider configured; nothing to test.")
        return 1
    print(f"provider: {llm.provider()}  model: {llm.model_name()}")
    idx = IdentityIndex()
    known_emails = set(idx.by_email)
    known_users = set(idx.users)
    pngs = [r for r in load_manifest() if r["file_path"].lower().endswith(".png")]

    t0 = time.time()
    units, notes = media.extract_media(pngs)
    print(f"\n== screenshots: {len(pngs)} files, {sum(len(v) for v in units.values())} records, "
          f"{time.time() - t0:.0f}s")
    problems = 0
    for row in pngs:
        recs = units.get(row["file_id"])
        if recs is None:
            print(f"  {row['file_id']}: NOT EXTRACTED")
            problems += 1
            continue
        ids, unknown, bad = [], [], []
        for u in recs:
            if not RESOURCE_RE.fullmatch(u.resource_id):
                bad.append(f"id {u.resource_id}")
            if u.fields["result"] not in ("", "FOUND", "NOT_FOUND", "IDENTIFIABLE", "ANONYMIZED", "REDACTED",
                                          "ACTIVE", "DISABLED", "RESTORABLE", "BLOCKED"):
                bad.append(f"state {u.fields['result']}")
            if not ISO_RE.match(u.observed_at) or u.observed_at > row["captured_at"]:
                bad.append(f"time {u.observed_at}")
            for ident in u.fields["identifiers"]:
                if EMAIL_RE.fullmatch(ident.strip()):
                    (ids if norm_email(ident) in known_emails else unknown).append(ident)
                elif re.fullmatch(r"user_\d+", ident.strip()):
                    (ids if ident.strip() in known_users else unknown).append(ident)
                elif HASH_RE.fullmatch(ident.strip().lower()) or PHONE_RE.fullmatch(ident.strip()):
                    ids.append(ident)
        problems += len(bad)
        print(f"  {row['file_id']} {Path(row['file_path']).name}: {len(recs)} records "
              f"{sorted({u.resource_id for u in recs})[:6]} | known ids {len(ids)} | unknown {unknown[:3]} "
              f"| violations {bad[:3]}")
    for n in notes:
        print("  note:", n)

    texts = intent_set()
    out, inotes = intent.classify([t for t, _ in texts])
    print(f"\n== intent: {len(texts)} texts")
    agree_expected = sum(out[t]["authorizes_deletion"] == exp for t, exp in texts)
    agree_rule = sum(out[t]["authorizes_deletion"] == (intent.rule_label(t) == "DELETION") for t, _ in texts)
    sources = {out[t]["source"] for t, _ in texts}
    print(f"  source(s): {sources}  matches expected label: {agree_expected}/{len(texts)}  "
          f"agrees with keyword rule: {agree_rule}/{len(texts)}")
    for t, exp in texts:
        if out[t]["authorizes_deletion"] != exp:
            print(f"  MISMATCH expected={exp} got={out[t]['request_type']}: {t[:80]}")
    for n in inotes:
        print("  note:", n)

    print("\n== usage:", llm.USAGE.as_dict(), "| disabled:", llm.disabled_reason())
    return 0 if problems == 0 and llm.disabled_reason() is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
