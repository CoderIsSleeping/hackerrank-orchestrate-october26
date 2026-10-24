"""Identity resolution: request email -> stable user_id -> every identifier ever held."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field

from config import DATASET

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
PHONE_RE = re.compile(r"\+\d[\d \-()]{6,}\d")


def norm_email(v: str) -> str:
    return v.strip().lower()


def norm_phone(v: str) -> str:
    """Phones are written with varying spacing and dashes; compare digits only."""
    return re.sub(r"\D", "", v)


@dataclass
class Subject:
    user_id: str | None
    emails: set = field(default_factory=set)
    phones: set = field(default_factory=set)
    hashes: set = field(default_factory=set)
    names: set = field(default_factory=set)

    def tokens(self) -> set:
        """Normalized identifier tokens, comparable with text_tokens()."""
        out = {f"e:{e}" for e in self.emails} | {f"h:{h}" for h in self.hashes} | {f"p:{p}" for p in self.phones}
        if self.user_id:
            out.add(f"u:{self.user_id}")
        return out

    def matches_text(self, text: str) -> bool:
        """True when the text contains one of the subject's exact identifiers."""
        if any(norm_email(e) in self.emails for e in EMAIL_RE.findall(text)):
            return True
        low = text.lower()
        if any(h in self.hashes for h in HASH_RE.findall(low)):
            return True
        if self.phones and any(norm_phone(p) in self.phones for p in PHONE_RE.findall(text)):
            return True
        return bool(self.user_id) and re.search(rf"\b{re.escape(self.user_id)}\b", text) is not None


USER_RE = re.compile(r"\buser_\d+\b")


def text_tokens(text: str) -> set:
    """Every exact identifier in a piece of evidence, normalized the same way as Subject.tokens()."""
    out = {f"e:{norm_email(e)}" for e in EMAIL_RE.findall(text)}
    out |= {f"h:{h}" for h in HASH_RE.findall(text.lower())}
    out |= {f"p:{norm_phone(p)}" for p in PHONE_RE.findall(text)}
    out |= {f"u:{u}" for u in USER_RE.findall(text)}
    return out


class IdentityIndex:
    def __init__(self):
        with open(DATASET / "user_identities.csv", encoding="utf-8") as f:
            self.users = {r["user_id"]: r for r in csv.DictReader(f)}
        with open(DATASET / "user_audit_log.csv", encoding="utf-8") as f:
            self.audit = list(csv.DictReader(f))
        self.by_email: dict[str, str] = {}
        for uid, r in self.users.items():
            self.by_email[norm_email(r["email"])] = uid
        for a in self.audit:
            if a["field_name"] == "email":
                for v in (a["old_value"], a["new_value"]):
                    self.by_email.setdefault(norm_email(v), a["user_id"])

    def resolve(self, email: str) -> tuple[Subject, list[str]]:
        """Return the subject and the audit_ids that link a historical email to it."""
        uid = self.by_email.get(norm_email(email))
        subject = Subject(uid, {norm_email(email)})
        links = []
        if uid is None:
            return subject, links
        cur = self.users.get(uid)
        if cur:
            subject.emails.add(norm_email(cur["email"]))
            subject.phones.add(norm_phone(cur["phone"]))
            subject.hashes.add(cur["email_hash"].lower())
            subject.names.add(cur["name"])
        for a in self.audit:
            if a["user_id"] != uid:
                continue
            for v in (a["old_value"], a["new_value"]):
                if a["field_name"] == "email":
                    subject.emails.add(norm_email(v))
                elif a["field_name"] == "phone":
                    subject.phones.add(norm_phone(v))
                elif a["field_name"] == "email_hash":
                    subject.hashes.add(v.lower())
            if a["field_name"] == "email" and norm_email(a["old_value"]) == norm_email(email):
                links.append(a["audit_id"])
        subject.phones.discard("")
        return subject, links
