"""Multimodal evidence: Claude reads PDFs and screenshots into the same Unit shape as text files.

The model transcribes facts only (resource ids, identifiers, states). Everything it returns is
validated: resource ids must match the platform's id pattern, states must come from a fixed enum,
and identifiers are re-checked downstream by exact matching. One call per file, cached.
"""
from __future__ import annotations

import json
import re

import llm
from config import EVIDENCE_ROOT
from evidence_extract import RESOURCE_RE, Unit

STATES = ["FOUND", "NOT_FOUND", "IDENTIFIABLE", "ANONYMIZED", "REDACTED", "ACTIVE", "DISABLED",
          "RESTORABLE", "BLOCKED", "PRESENT", "UNKNOWN"]

SYSTEM_PROMPT = """You transcribe internal business documents and application screenshots of an online
shop into structured records for a data-deletion audit. Report only what is visibly written.

For every platform resource shown (ids look like account_123, search_45, analytics_6, support_7,
export_8, data_flow_9, backup_10, audit_record_11, tombstone_12, object_13, marketing_14, cache_15),
emit one record with:
- resource_id exactly as written;
- system: the owning system if shown (e.g. account_service, support_platform), else "";
- record_type: the data category if shown (e.g. USER_PROFILE, SUPPORT_ATTACHMENT, SECURITY_AUDIT_LOG), else "";
- state: what the document says about the record now (FOUND, NOT_FOUND, IDENTIFIABLE, ANONYMIZED,
  REDACTED, ACTIVE, DISABLED, RESTORABLE, BLOCKED, PRESENT) or UNKNOWN;
- observed_at: the timestamp the document gives for that record, ISO-8601 UTC, else "";
- identifiers: every person identifier tied to that record on the page (emails, phone numbers,
  user_ids like user_17, full names, 64-hex email hashes), copied exactly;
- facts: one short sentence of any other relevant fact (retention date, legal hold, restore setting,
  "belongs only to", enabled/disabled).
Also list page-level identifiers that describe the whole page's subject in page_identifiers.
Do not infer or invent ids. If no resource ids are visible, return an empty records list."""

SCHEMA = {
    "type": "object",
    "properties": {
        "page_identifiers": {"type": "array", "items": {"type": "string"}},
        "page_timestamp": {"type": "string"},
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string"},
                    "system": {"type": "string"},
                    "record_type": {"type": "string"},
                    "state": {"type": "string", "enum": STATES},
                    "observed_at": {"type": "string"},
                    "identifiers": {"type": "array", "items": {"type": "string"}},
                    "facts": {"type": "string"},
                },
                "required": ["resource_id", "system", "record_type", "state", "observed_at", "identifiers", "facts"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["page_identifiers", "page_timestamp", "records"],
    "additionalProperties": False,
}

MEDIA_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg")


def catalog_aliases() -> dict[str, str]:
    """Display names the data catalog gives resources, e.g. 'object_19 (delivery_confirmation_19.pdf)'.

    Screenshots often show only the display name; mapping it back to the platform id lets that evidence
    count. Only exact aliases written in LINK_RESOURCE_IDENTITY rows are used.
    """
    import csv
    from config import DATASET
    aliases = {}
    with open(DATASET / "system_activity.csv", encoding="utf-8") as f:
        for a in csv.DictReader(f):
            if a["event_type"] == "LINK_RESOURCE_IDENTITY":
                m = re.search(rf"{re.escape(a['resource_id'])} \(([^)]+)\)", a["details"])
                if m and m.group(1) != a["resource_id"]:
                    aliases[m.group(1).strip().lower()] = a["resource_id"]
    return aliases


def extract_media(manifest: list[dict]) -> tuple[dict[str, list[Unit]], list[str]]:
    """Return {file_id: [Unit, ...]} for every PDF/image, plus notes about rejected output."""
    units: dict[str, list[Unit]] = {}
    notes: list[str] = []
    aliases = catalog_aliases()
    if not llm.available():
        return units, ["media: no LLM provider (ANTHROPIC_API_KEY / GEMINI_API_KEY) set; screenshots were not read "
                       "(PDF text layers are still read offline)"]
    for row in manifest:
        path = EVIDENCE_ROOT / row["file_path"]
        if path.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        if not llm.available():
            notes.append(f"media {row['file_id']}: skipped, Claude unavailable ({llm.disabled_reason()})")
            continue
        try:
            content = [llm.file_block(path), {"type": "text", "text": f"File: {path.name}. Transcribe it."}]
            result = llm.call_json(SYSTEM_PROMPT, content, SCHEMA, effort="medium")
        except Exception as e:     # one unreadable file must not stop the audit
            notes.append(f"media {row['file_id']}: extraction failed ({type(e).__name__}); file skipped")
            continue
        page_ids = result.get("page_identifiers", [])
        out = []
        for rec in result["records"]:
            rid = rec["resource_id"].strip()
            if not RESOURCE_RE.fullmatch(rid) and rid.lower() in aliases:
                notes.append(f"media {row['file_id']}: {rid!r} mapped to {aliases[rid.lower()]} via catalog alias")
                rid = aliases[rid.lower()]
            if not RESOURCE_RE.fullmatch(rid):
                notes.append(f"media {row['file_id']}: rejected id {rid!r}")
                continue
            fields = {"system": rec["system"], "record_type": rec["record_type"].upper(),
                      "result": rec["state"] if rec["state"] not in ("UNKNOWN", "PRESENT") else "",
                      "identifiers": rec["identifiers"], "facts": rec["facts"], "_listed": True}
            if rec["state"] == "PRESENT":
                fields["result"] = "FOUND"
            # Record-level identifiers ground the record; page-level ones describe the page subject.
            text = json.dumps({"resource_id": rid, "identifiers": rec["identifiers"] + page_ids,
                               "facts": rec["facts"]}, ensure_ascii=False)
            observed = rec["observed_at"] or result.get("page_timestamp") or row["captured_at"]
            observed = observed if observed <= row["captured_at"] else row["captured_at"]
            out.append(Unit(row["file_id"], row["captured_at"], observed, rid, fields, rec["system"], text))
        units[row["file_id"]] = out
    return units, notes
