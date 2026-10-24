"""Evidence extraction: turn every evidence file into normalized *units*.

A unit is one observation about one resource in one file: the merged fields
(identifiers, status values) describing that resource at a point in time.
Format-specific parsing stops here; everything downstream reads units only.

Also records *scopes*: complete listings of a collection (SQL tables, snapshot
bundle collections) so that a resource missing from a listing can be used as
evidence that it is absent.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field

from config import DATASET, EVIDENCE_ROOT

RESOURCE_RE = re.compile(
    r"\b(?:data_flow|audit_record|legal_hold|email_log|account|search|analytics|support|export|"
    r"backup|tombstone|cache|marketing|object|attachment|telemetry)_\d+\b"
)
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")
TRACE_RE = re.compile(r"\b(?:tr|rb)-[A-Za-z0-9-]+")
KV_RE = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|\S+)')
SERVICE_RE = re.compile(r"\b([a-z]+(?:_[a-z]+)*_(?:service|platform|replica))\b")


@dataclass
class Unit:
    file_id: str
    captured_at: str            # file capture time from the manifest
    observed_at: str            # time the observation itself refers to
    resource_id: str
    fields: dict = field(default_factory=dict)
    source: str = ""            # container: table / collection / service
    text: str = ""              # raw text, for identifier matching


@dataclass
class Scope:
    """A complete listing of one collection at one time inside one file."""
    file_id: str
    captured_at: str            # time the listing describes
    container: str
    prefixes: set               # resource prefixes this listing covers
    resource_ids: set
    file_captured_at: str = ""  # manifest capture time: the listing is unusable before this


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('\\"', '"')
    return v


def _prefix(resource_id: str) -> str:
    return resource_id.rsplit("_", 1)[0]


def load_manifest() -> list[dict]:
    with open(DATASET / "evidence_files.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- logs

def _parse_log_line(line: str) -> dict:
    out: dict = {}
    m = TS_RE.match(line)
    if m:
        out["_time"] = m.group(1)
    brace = line.find("{")
    if brace != -1 and line.rstrip().endswith("}"):
        try:
            out.update({k: v for k, v in json.loads(line[brace:]).items()})
        except json.JSONDecodeError:
            pass
    for k, v in KV_RE.findall(line):
        v = _unquote(v)
        if k == "message" and "=" in v:          # cloud-runtime nests key=value pairs inside message
            for k2, v2 in KV_RE.findall(v):
                out.setdefault(k2, _unquote(v2))
        out.setdefault(k, v)
    if "event" not in out:                         # "[svc] [trace] event:" and "| svc | trace | event |" styles
        m = re.search(r"\] ([a-z_]+):", line) or re.search(r"\| ([a-z_]+) \|", line)
        if m:
            out["event"] = m.group(1)
    t = TRACE_RE.search(line)
    if t:
        out["_trace"] = out.get("trace_id") or out.get("requestId") or t.group(0)
    if "service" not in out:
        s = SERVICE_RE.search(line)
        if s:
            out["service"] = s.group(1)
    return out


def extract_log(file_id: str, captured_at: str, text: str) -> list[Unit]:
    groups: dict[str, list[tuple[dict, str]]] = {}
    standalone: list[tuple[dict, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        rec = _parse_log_line(line)
        if rec.get("_trace"):
            groups.setdefault(rec["_trace"], []).append((rec, line))
        elif RESOURCE_RE.search(line):
            standalone.append((rec, line))
    units = []
    for lines in list(groups.values()) + [[x] for x in standalone]:
        merged: dict = {}
        decisive: dict = {}
        events = []
        resource, observed = None, None
        for rec, line in lines:
            for k, v in rec.items():
                if k.endswith("_key"):
                    merged.setdefault(k, v)     # keep the lookup identifier from the *_started line
                else:
                    merged[k] = v
            if rec.get("event"):
                events.append(rec["event"])
            m = RESOURCE_RE.search(line)
            rid = rec.get("resource_id") or (m.group(0) if m else None)
            if rid:
                resource, observed = rid, rec.get("_time", captured_at)
                decisive.update(rec)
        if not resource:
            continue
        # Auxiliary lines in the same trace (cache_lookup, connection_acquired) also carry
        # result-like fields; the lines that name the resource are authoritative.
        merged.update({k: v for k, v in decisive.items() if not k.endswith("_key")})
        merged["_events"] = events
        units.append(Unit(file_id, captured_at, observed or captured_at, resource, merged,
                          merged.get("service", ""), "\n".join(l for _, l in lines)))
    return units


# ---------------------------------------------------------------- JSON

_CONTEXT_KEYS = ("captured_at", "recorded_at", "observed_at", "exported_at", "data_store",
                 "collection", "system", "source", "origin")
_TIME_KEYS = ("observed_at", "recorded_at", "captured_at", "exported_at")


def extract_json(file_id: str, captured_at: str, data, units: list, scopes: list,
                 ctx: dict | None = None, path: str = "") -> None:
    ctx = dict(ctx or {})
    if isinstance(data, dict):
        for k in _CONTEXT_KEYS:
            if isinstance(data.get(k), str):
                ctx[k] = data[k]
        if isinstance(data.get("resource_id"), str):
            flat = _flatten(data)
            observed = next((flat[k] for k in _TIME_KEYS if isinstance(flat.get(k), str)),
                            next((ctx[k] for k in _TIME_KEYS if k in ctx), captured_at))
            fields = {**{k: v for k, v in ctx.items()}, **flat, "_listed": True}
            units.append(Unit(file_id, captured_at, observed, data["resource_id"], fields,
                              ctx.get("data_store") or ctx.get("system") or path,
                              json.dumps(data, ensure_ascii=False)))
            return
        # A snapshot collection lists every record of a data store at one time -> complete scope.
        if "data_store" in data and isinstance(data.get("records"), list):
            ids = {r["resource_id"] for r in data["records"] if isinstance(r, dict) and "resource_id" in r}
            prefixes = {_prefix(i) for i in ids} or _prefixes_for_store(data["data_store"])
            scopes.append(Scope(file_id, data.get("captured_at", captured_at),
                                f'{data["data_store"]}/{data.get("collection", "")}', prefixes, ids, captured_at))
        for k, v in data.items():
            extract_json(file_id, captured_at, v, units, scopes, ctx, k if path == "" else f"{path}.{k}")
    elif isinstance(data, list):
        for item in data:
            extract_json(file_id, captured_at, item, units, scopes, ctx, path)


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
            out.setdefault(k, None) if not v else None
        else:
            out[key] = v
    return out


def _prefixes_for_store(store: str) -> set:
    from config import RESOURCE_PREFIXES
    return {p for p, (system, _) in RESOURCE_PREFIXES.items() if system == store}


# ---------------------------------------------------------------- SQL

def extract_sql(file_id: str, captured_at: str, text: str) -> tuple[list[Unit], list[Scope]]:
    m = re.search(r"Captured at:\s*(\S+)", text)
    snap_time = m.group(1) if m else captured_at
    db = sqlite3.connect(":memory:")
    db.executescript(text)
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    scope_times = {}
    if "snapshot_scope" in tables:
        scope_times = dict(db.execute("SELECT origin_system, captured_at FROM snapshot_scope"))
        snap_time = max(scope_times.values(), default=snap_time)
    units, scopes = [], []
    for table in tables:
        if table == "snapshot_scope":
            continue
        cur = db.execute(f'SELECT * FROM "{table}"')
        cols = [c[0] for c in cur.description]
        ids = set()
        prefixes = set()
        for row in cur:
            rec = dict(zip(cols, row))
            if isinstance(rec.get("record_json"), str):
                rec.update(_flatten(json.loads(rec.pop("record_json"))))
            rid = rec.get("resource_id")
            if not rid:
                continue
            ids.add(rid)
            prefixes.add(_prefix(rid))
            rec["_table"] = table
            units.append(Unit(file_id, captured_at, snap_time, rid, rec, table,
                              json.dumps(rec, ensure_ascii=False, default=str)))
        if not prefixes:
            prefixes = _table_prefixes(table)
        if scope_times:
            # Only systems listed in snapshot_scope were captured; an empty table for any
            # other system says nothing about absence.
            prefixes = prefixes & set().union(*(_prefixes_for_store(s) for s in scope_times))
        if prefixes:
            scopes.append(Scope(file_id, snap_time, table, prefixes, ids, captured_at))
    db.close()
    return units, scopes


_TABLE_HINTS = {"profiles": "account", "search": "search", "analytics": "analytics", "support": "support",
                "exports": "export", "connector": "data_flow", "backup": "backup", "security_audit": "audit_record"}


def _table_prefixes(table: str) -> set:
    return {p for hint, p in _TABLE_HINTS.items() if hint in table}


# ---------------------------------------------------------------- text records

HANDOVER_RE = re.compile(r"^- (\S+) \| (\S+) \| (\S+) \| (\S+) \| (\S+) \| (.+)$")


def extract_text(file_id: str, captured_at: str, text: str) -> list[Unit]:
    units = []
    for line in text.splitlines():
        m = HANDOVER_RE.match(line.strip())
        if m:
            t, system, rid, category, state, ref = m.groups()
            units.append(Unit(file_id, captured_at, t, rid,
                              {"system": system, "record_type": category, "result": state,
                               "subject_reference": ref}, system, line))
        elif RESOURCE_RE.search(line):
            units.extend(extract_log(file_id, captured_at, line))
    return units


# ---------------------------------------------------------------- PDF text layer

def extract_pdf(file_id: str, captured_at: str, path) -> list[Unit]:
    """Local, offline reading of a PDF's text layer (business documents: attachments, extracts).

    Every resource id in the document becomes a unit that records the document as showing the record.
    Grounding text: the whole document when it concerns a single person; otherwise only the lines
    around each id, so a multi-person register cannot tie one person's identifiers to another's record.
    """
    try:
        from pypdf import PdfReader
        text = "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    except Exception:            # missing library or unreadable file: the media pass may still cover it
        return []
    from identity import text_tokens
    people = {t for t in text_tokens(text) if t.startswith(("e:", "u:"))}
    lines = text.splitlines()
    units = []
    for rid in sorted(set(RESOURCE_RE.findall(text))):
        if len(people) <= 2:            # one subject: an email and/or a user_id
            context = text
        else:
            idx = [i for i, l in enumerate(lines) if rid in l]
            context = "\n".join(lines[max(0, i - 3): i + 4] for i in idx) if idx else ""
        record_type = "SUPPORT_ATTACHMENT" if "attachment" in text.lower() and rid.startswith("support_") else ""
        fields = {"_listed": True, "result": "FOUND", "document": path.name}
        if record_type:
            fields["record_type"] = record_type
        units.append(Unit(file_id, captured_at, captured_at, rid, fields, "document", context))
    return units


# ---------------------------------------------------------------- driver

def extract_all(media_units: dict | None = None) -> tuple[list[Unit], list[Scope], list[dict]]:
    """Extract every text-based evidence file. Media (PDF/PNG) units are supplied by the caller."""
    units: list[Unit] = []
    scopes: list[Scope] = []
    manifest = load_manifest()
    for row in manifest:
        fid, path, captured = row["file_id"], EVIDENCE_ROOT / row["file_path"], row["captured_at"]
        suffix = path.suffix.lower()
        if suffix in (".pdf", ".png", ".jpg", ".jpeg"):
            if suffix == ".pdf":
                units.extend(extract_pdf(fid, captured, path))
            units.extend((media_units or {}).get(fid, []))
            continue
        text = path.read_text(encoding="utf-8")
        if suffix == ".sql":
            u, s = extract_sql(fid, captured, text)
            units.extend(u)
            scopes.extend(s)
        elif suffix == ".json":
            extract_json(fid, captured, json.loads(text), units, scopes)
        elif suffix == ".log":
            units.extend(extract_log(fid, captured, text))
        else:
            units.extend(extract_text(fid, captured, text))
    return units, scopes, manifest
