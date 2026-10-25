"""Targeted regression tests for linking, state and policy behaviour.

    LLM_PROVIDER=none python code/evaluation/test_regression.py -v

Synthetic inputs exercise the real components (GlobalLinker, Investigator, planner); invariance tests
run on the real dataset. No sample answers are used, and no API calls are made.
"""
from __future__ import annotations

import csv
import os
import random
import sys
import unittest
from pathlib import Path

os.environ["LLM_PROVIDER"] = "none"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from config import DATASET  # noqa: E402
from evidence_extract import Scope, Unit, extract_all  # noqa: E402
from identity import IdentityIndex  # noqa: E402
from investigator import Investigator  # noqa: E402
from linker import GlobalLinker  # noqa: E402
from planner import build_steps  # noqa: E402

IDX = IdentityIndex()
USERS = list(IDX.users.values())


def req(case_id, user, reported_status="COMPLETE", requested="2026-09-01T08:00:00Z",
        reported="2026-09-02T12:00:00Z", audit="2026-09-10T12:00:00Z"):
    return {"case_id": case_id, "email": user["email"], "requested_at": requested, "reported_status": reported_status,
            "reported_at": reported, "audit_at": audit, "request_text": "Please delete my account."}


def primary(aid, rid, t, result):
    return {"activity_id": aid, "event_time": t, "event_type": "DELETE_RECORD", "source_system": "deletion_worker",
            "target_system": "account_service", "resource_id": rid, "reported_result": result,
            "details": f"Deletion worker submitted primary profile {rid} to account_service."}


def unit(fid, rid, t, fields, text="", captured=None):
    return Unit(fid, captured or t, t, rid, {"_listed": True, **fields}, "test", text or rid)


class StubLinker:
    def __init__(self, links):
        self.links = links

    def links_for(self, _case_id):
        return dict(self.links)


def audit_case(units, scopes, links, audit="2026-09-10T12:00:00Z", user=None):
    inv = Investigator(units, scopes)
    inv.linker = StubLinker(links)
    result = inv.investigate(req("case_t", user or USERS[0], audit=audit))
    return build_steps(result.decisions)


class LinkerTests(unittest.TestCase):
    def test_shared_timestamp_tie_stays_unresolved(self):
        a, b = req("case_a", USERS[0]), req("case_b", USERS[1])
        acts = [primary("act_1", "account_9001", "2026-09-02T11:00:00Z", "SUCCESS"),
                primary("act_2", "account_9002", "2026-09-02T11:05:00Z", "SUCCESS")]
        lk = GlobalLinker([a, b], [], acts, IDX)
        self.assertEqual(lk.links_for("case_a"), {})
        self.assertEqual(lk.links_for("case_b"), {})

    def test_status_consistency_breaks_tie(self):
        a, b = req("case_a", USERS[0], "COMPLETE"), req("case_b", USERS[1], "NOT_STARTED")
        acts = [primary("act_1", "account_9001", "2026-09-02T11:00:00Z", "FAILED"),
                primary("act_2", "account_9002", "2026-09-02T11:05:00Z", "SUCCESS")]
        lk = GlobalLinker([a, b], [], acts, IDX)
        self.assertIn("account_9002", lk.links_for("case_a"))
        self.assertIn("account_9001", lk.links_for("case_b"))

    def test_record_grounded_to_another_person_is_excluded(self):
        a, b = req("case_a", USERS[0]), req("case_b", USERS[1])
        other = unit("ev_1", "account_9001", "2026-09-01T09:00:00Z", {"email": USERS[1]["email"]},
                     text=f'{{"email": "{USERS[1]["email"]}"}}')
        acts = [primary("act_1", "account_9001", "2026-09-02T11:00:00Z", "SUCCESS")]
        lk = GlobalLinker([a, b], [other], acts, IDX)
        self.assertNotIn("account_9001", lk.links_for("case_a"))
        self.assertIn("account_9001", lk.links_for("case_b"))

    def test_historical_identifier_resolves_to_same_person(self):
        changed = next(a for a in IDX.audit if a["field_name"] == "email")
        old, _ = IDX.resolve(changed["old_value"])
        cur, _ = IDX.resolve(IDX.users[changed["user_id"]]["email"])
        self.assertEqual(old.user_id, cur.user_id)
        self.assertEqual(old.tokens(), cur.tokens())

    def test_sync_resurrection_path_is_linked(self):
        a = req("case_a", USERS[0])
        rec = unit("ev_1", "search_9001", "2026-09-01T09:00:00Z", {"email": USERS[0]["email"]},
                   text=f'{{"email": "{USERS[0]["email"]}"}}')
        sync = {"activity_id": "act_9", "event_time": "2026-09-03T10:00:00Z", "event_type": "SYNC_RECORD",
                "source_system": "integration_service", "target_system": "search_service",
                "resource_id": "data_flow_9001", "reported_result": "SUCCESS",
                "details": "Active connector repopulated downstream resource search_9001."}
        lk = GlobalLinker([a], [rec], [sync], IDX)
        self.assertIn("data_flow_9001", lk.links_for("case_a"))

    def test_insufficient_evidence_links_nothing(self):
        a = req("case_a", USERS[0])
        stray = unit("ev_1", "account_9001", "2026-09-01T09:00:00Z", {"email": "nobody@example.invalid"},
                     text='{"email": "nobody@example.invalid"}')
        lk = GlobalLinker([a], [stray], [], IDX)
        self.assertEqual(lk.links_for("case_a"), {})


class StateAndPolicyTests(unittest.TestCase):
    def test_direct_observation_beats_later_listing_absence(self):
        seen = unit("ev_1", "account_9001", "2026-09-05T09:00:00Z", {"email": "x@y.z"})
        listing = Scope("ev_2", "2026-09-08T09:00:00Z", "customer_profiles", {"account"}, set(), "2026-09-08T09:00:00Z")
        status, steps = audit_case([seen], [listing], {"account_9001": "ev_1"})
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual(steps[0]["operation"], "REPORT_REMAINING_RECORD")
        self.assertEqual(steps[0]["evidence_id"], "ev_1")

    def test_listing_absence_used_when_nothing_observed_directly(self):
        listing = Scope("ev_2", "2026-09-08T09:00:00Z", "customer_profiles", {"account"}, set(), "2026-09-08T09:00:00Z")
        status, steps = audit_case([], [listing], {"account_9001": "act_1"})
        self.assertEqual((status, steps[0]["operation"], steps[0]["evidence_id"]), ("COMPLETE", "VERIFY_RECORD_ABSENT", "ev_2"))

    def test_evidence_after_audit_is_ignored(self):
        late = unit("ev_9", "account_9001", "2026-09-20T09:00:00Z", {"email": "x@y.z"})
        status, steps = audit_case([late], [], {"account_9001": "act_1"})
        self.assertTrue(all(s["evidence_id"] != "ev_9" for s in steps))
        self.assertEqual(steps, [])      # unobserved and the case is not incomplete: nothing is claimed

    def test_retained_audit_record_verified_present_never_deleted(self):
        rec = unit("ev_1", "audit_record_9001", "2026-09-05T09:00:00Z", {"subject_reference": "user_01"})
        status, steps = audit_case([rec], [], {"audit_record_9001": "ev_1"})
        self.assertEqual((status, [s["operation"] for s in steps]), ("COMPLETE", ["VERIFY_RECORD_PRESENT"]))

    def test_identifiable_analytics_requires_anonymization(self):
        rec = unit("ev_1", "analytics_9001", "2026-09-05T09:00:00Z", {"email": "x@y.z", "user_id": "user_01"})
        status, steps = audit_case([rec], [], {"analytics_9001": "ev_1"})
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual([s["operation"] for s in steps],
                         ["REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"])

    def test_anonymized_analytics_only_verified(self):
        rec = unit("ev_1", "analytics_9001", "2026-09-05T09:00:00Z", {"email": None, "user_id": None})
        status, steps = audit_case([rec], [], {"analytics_9001": "ev_1"})
        self.assertEqual((status, [s["operation"] for s in steps]), ("COMPLETE", ["VERIFY_RECORD_ANONYMIZED"]))

    def test_unobserved_record_only_future_check_when_incomplete(self):
        seen = unit("ev_1", "search_9001", "2026-09-05T09:00:00Z", {"email": "x@y.z"})
        status, steps = audit_case([seen], [], {"search_9001": "ev_1", "account_9001": "act_1"})
        acct = [s for s in steps if s["resource_id"] == "account_9001"]
        self.assertEqual(status, "INCOMPLETE")
        self.assertEqual([(s["operation"], s["evidence_id"]) for s in acct], [("VERIFY_RECORD_ABSENT", "")])


def transcribed(rid, label, state="ACTIVE", identifiers=(), facts=""):
    """A screenshot record as the model returns it, converted by the production code path."""
    import media
    rec = {"resource_id": rid, "system": "", "record_type": label, "state": state,
           "observed_at": "2026-09-05T09:00:00Z", "identifiers": list(identifiers), "facts": facts}
    row = {"file_id": "img_1", "file_path": "evidence/images/x.png", "captured_at": "2026-09-05T10:00:00Z"}
    unit, _ = media.record_to_unit(rec, row, [], "", {})
    return unit


def audit_with_notes(units, links):
    inv = Investigator(units, [])
    inv.linker = StubLinker(links)
    result = inv.investigate(req("case_t", USERS[0]))
    status, steps = build_steps(result.decisions)
    return status, [s["operation"] for s in steps], result.notes


RULES = sorted({r["data_category"] for r in csv.DictReader(open(DATASET / "deletion_rules.csv", encoding="utf-8"))})


class MediaPolicyTests(unittest.TestCase):
    def test_model_label_is_never_authoritative(self):
        u = transcribed("analytics_9001", "USER_PROFILE", identifiers=["Jane Roe", "user_01"])
        self.assertNotIn("record_type", u.fields)
        self.assertEqual(u.fields["llm_record_type"], "USER_PROFILE")

    def test_conflicting_label_is_flagged_and_policy_wins(self):
        u = transcribed("analytics_9001", "USER_PROFILE", identifiers=["jane@example.invalid", "user_01"])
        status, ops, notes = audit_with_notes([u], {"analytics_9001": "img_1"})
        self.assertEqual(ops, ["REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"])
        self.assertNotIn("DELETE_RECORD", ops)
        self.assertTrue(any("conflicts with authoritative ANALYTICS_PROFILE" in n for n in notes))

    def test_identifiable_analytics_from_screenshot_requires_anonymization(self):
        u = transcribed("analytics_9001", "", identifiers=["user_01"])       # attribution only
        _, ops, _ = audit_with_notes([u], {"analytics_9001": "img_1"})
        self.assertEqual(ops, ["REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"])

    def test_anonymized_analytics_from_screenshot_only_verified(self):
        u = transcribed("analytics_9001", "ANALYTICS_PROFILE", state="ANONYMIZED")
        status, ops, notes = audit_with_notes([u], {"analytics_9001": "img_1"})
        self.assertEqual((status, ops), ("COMPLETE", ["VERIFY_RECORD_ANONYMIZED"]))
        self.assertFalse(any("conflicts" in n for n in notes))

    def test_consistent_label_behaves_normally(self):
        u = transcribed("support_9001", "SUPPORT_MESSAGE", identifiers=["jane@example.invalid"],
                        facts="Support ticket conversation about an order.")
        _, ops, notes = audit_with_notes([u], {"support_9001": "img_1"})
        self.assertEqual(ops, ["REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"])
        self.assertFalse(any("conflicts" in n for n in notes))

    def test_missing_or_unknown_label_falls_back_to_record_family(self):
        for label in ("", "DATA_EXPORT", "SOMETHING_ELSE"):
            u = transcribed("export_9001", label)
            self.assertNotIn("direct_identifiers", u.fields)                # nothing listed -> nothing claimed
            _, ops, notes = audit_with_notes([u], {"export_9001": "img_1"})
            self.assertEqual(ops, ["REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"], label)
            self.assertFalse(any("conflicts" in n for n in notes), label)

    def test_no_model_label_can_bypass_deletion_rules(self):
        # For each authoritative family, the operations allowed by deletion_rules.csv.
        allowed = {
            "audit_record_9001": {"VERIFY_RECORD_PRESENT"},                                     # RETAIN
            "analytics_9001": {"REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"},
            "backup_9001": {"REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"},
            "data_flow_9001": {"REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"},
        }
        for rid, ok in allowed.items():
            for label in RULES:
                u = transcribed(rid, label, identifiers=["jane@example.invalid", "user_01"])
                _, ops, _ = audit_with_notes([u], {rid: "img_1"})
                self.assertTrue(set(ops) <= ok, f"{rid} with model label {label}: {ops}")


class InvarianceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units, cls.scopes, _ = extract_all()
        rows = lambda n: list(csv.DictReader(open(DATASET / n, encoding="utf-8")))  # noqa: E731
        cls.requests = rows("deletion_requests.csv") + rows("sample_deletion_requests.csv")
        cls.activity = rows("system_activity.csv")
        cls.base = GlobalLinker(cls.requests, cls.units, cls.activity, IDX)

    def test_row_order_does_not_change_links(self):
        for seed in (7, 11, 23):
            rnd = random.Random(seed)
            reqs, units, acts = self.requests[:], self.units[:], self.activity[:]
            rnd.shuffle(reqs), rnd.shuffle(units), rnd.shuffle(acts)
            shuffled = GlobalLinker(reqs, units, acts, IDX)
            for r in self.requests:
                self.assertEqual(shuffled.links_for(r["case_id"]), self.base.links_for(r["case_id"]),
                                 f"seed {seed} {r['case_id']}")

    def test_removing_unrelated_activity_does_not_change_links(self):
        noise = {"INDEX_RECORD", "REFRESH_CACHE", "SEND_EMAIL", "UPDATE_RECORD", "CREATE_EXPORT"}
        trimmed = GlobalLinker(self.requests, self.units, [a for a in self.activity if a["event_type"] not in noise], IDX)
        for r in self.requests:
            self.assertEqual(trimmed.links_for(r["case_id"]), self.base.links_for(r["case_id"]), r["case_id"])


if __name__ == "__main__":
    unittest.main()
