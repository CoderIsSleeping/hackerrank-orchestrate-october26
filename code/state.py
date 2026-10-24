"""Interpret one evidence unit as a resource state, and infer a resource's data category.

States:
  IDENTIFIABLE  record exists and still carries direct identifiers / attribution
  DEIDENTIFIED  record exists with direct identifiers removed (redacted / anonymized)
  PRESENT       record exists (retained categories, where identifiability is irrelevant)
  ABSENT        record does not exist
  FLOW_ACTIVE / FLOW_DISABLED       data flows
  RESTORE_ALLOWED / RESTORE_BLOCKED backups
"""
from __future__ import annotations

import re

from evidence_extract import Unit

# Leaf field names that hold direct identifiers in any evidence schema (matched on the last
# path segment, so "profile.email" and "hits.documents[].email" are covered too).
DIRECT_ID_KEY = re.compile(r"(^|_)(email|phone|full_name|display_name|name|email_hash|matched_value)$")
# Same suffixes, but naming things other than people.
NOT_PERSONAL = {"file_name", "index_name", "connector_name", "event_name", "object_name", "archive_name",
                "collection_name", "table_name", "pool_name", "logger_name"}
# Fields that keep a record attributable to a user (anonymization must remove them).
ATTRIBUTION_KEY = re.compile(r"(^|_)(user_id|customer_reference)$")
# Lookup keys echo the search term, not the record content.
LOOKUP_KEYS = {"query", "lookup_key", "match_key", "subject_key", "scan_key", "owner_key", "correlation_key"}

_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() in ("", "null", "None", "[REDACTED]"))


def _walk(key: str, value):
    """Yield (leaf_key, value) for nested dicts/lists; leaf_key is the last path segment."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(k, v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(key, v)
    else:
        yield key.rsplit(".", 1)[-1], value


def _leaves(fields: dict):
    for k, v in fields.items():
        if k.startswith("_") or k in LOOKUP_KEYS:
            continue
        yield from _walk(k, v)


def has_direct_identifiers(fields: dict) -> bool:
    flag = str(fields.get("direct_identifiers", "")).strip()
    if flag in ("0", "1"):
        return flag == "1"
    return any(DIRECT_ID_KEY.search(k) and k not in NOT_PERSONAL and not _empty(v) for k, v in _leaves(fields))


def is_attributed(fields: dict) -> bool:
    return any(ATTRIBUTION_KEY.search(k) and not _empty(v) for k, v in _leaves(fields))


def _flag(fields: dict, *keys):
    for k in keys:
        v = fields.get(k)
        if not _empty(v):
            s = str(v).lower()
            if s in _TRUE:
                return True
            if s in _FALSE:
                return False
    return None


def interpret(unit: Unit, category: str) -> str | None:
    f = unit.fields
    result = str(f.get("result") or f.get("state") or "").upper()

    if category == "DATA_FLOW":
        if result == "ACTIVE":
            return "FLOW_ACTIVE"
        if result in ("DISABLED", "NOT_FOUND"):
            return "FLOW_DISABLED"
        enabled = _flag(f, "enabled", "scheduler_registered")
        if enabled is not None:
            return "FLOW_ACTIVE" if enabled else "FLOW_DISABLED"
        return None

    if category == "BACKUP_ARCHIVE":
        if result in ("ACTIVE", "RESTORABLE", "FOUND", "IDENTIFIABLE"):
            return "RESTORE_ALLOWED"
        if result == "BLOCKED":
            return "RESTORE_BLOCKED"
        allowed = _flag(f, "restore_allowed", "eligible")
        if allowed is not None:
            return "RESTORE_ALLOWED" if allowed else "RESTORE_BLOCKED"
        return None

    # Explicit status values written by the source system.
    if result in ("NOT_FOUND", "MISS"):
        return "ABSENT"
    if result == "HIT":
        result = "FOUND"
    if result in ("ANONYMIZED", "REDACTED"):
        return "DEIDENTIFIED"
    if result == "IDENTIFIABLE":
        return "IDENTIFIABLE"

    rows = str(f.get("rows", ""))
    status = str(f.get("http_status", f.get("status_code", "")))
    hits = str(f.get("hits.total", f.get("total_hits", "")))
    if rows == "0" or status == "404" or hits == "0" or _flag(f, "download_available", "available") is False:
        return "ABSENT"

    events = f.get("_events") or []
    read_back = any(e.endswith(("_read_completed", "_pii_scan", "_identity_scan")) for e in events)
    exists = (result == "FOUND" or rows not in ("", "0") or status == "200" or hits not in ("", "0")
              or "_table" in f or f.get("_listed") or read_back)
    if not exists:
        return None
    if category in ("SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"):
        return "PRESENT"
    if result == "FOUND" and not has_direct_identifiers(f):
        return "IDENTIFIABLE"          # a lookup by the subject's identifier matched this record
    if category in ("ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"):
        return "IDENTIFIABLE" if has_direct_identifiers(f) or is_attributed(f) else "DEIDENTIFIED"
    return "IDENTIFIABLE" if has_direct_identifiers(f) else "DEIDENTIFIED"


# ------------------------------------------------------------------ category

def infer_category(default: str, units: list[Unit], known: set) -> str:
    """Refine the prefix-based category using what the evidence says about the record."""
    for u in units:
        rt = str(u.fields.get("record_type", "")).upper()
        if rt in known:
            return rt
    if default == "SUPPORT_MESSAGE":
        # rule_05 keeps (redacts) only conversations with shared context; attachments and bare
        # contact copies have no such context and fall under deletion.
        text = " ".join(u.text.lower() for u in units)
        if "attachment" in text:
            return "SUPPORT_ATTACHMENT"
        if not any(k in text for k in ("message", "ticket", "conversation")):
            return "SUPPORT_ATTACHMENT"
    return default
