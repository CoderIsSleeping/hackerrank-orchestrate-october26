"""Paths and constants. Every constant carries the reason it has its value."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "dataset"
EVIDENCE_ROOT = DATASET
OUTPUT_PATH = REPO_ROOT / "output.csv"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"

# Claude model used for judgement calls (intent, multimodal extraction).
# Opus 5.5 is the current default Opus; effort is set per call site.
MODEL = "claude-opus-5-5"
# Optional Gemini provider (used only when GEMINI_API_KEY is set and no Anthropic key is).
# gemini-2.5-flash: stable, image+text input, answered on the free tier when probed; overridable.
import os as _os
GEMINI_MODEL = _os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# USD per 1M tokens for MODEL (Anthropic first-party pricing), used only for usage_report.md.
PRICE_IN_PER_M = 4.00
PRICE_OUT_PER_M = 20.00

# resource_id prefix -> (system_id, data_category).
# Derived from the system ids used in sample_output.csv and the categories in deletion_rules.csv.
RESOURCE_PREFIXES = {
    "account": ("account_service", "USER_PROFILE"),
    "search": ("search_service", "SEARCH_INDEX_ENTRY"),
    "analytics": ("analytics_platform", "ANALYTICS_PROFILE"),
    "support": ("support_platform", "SUPPORT_MESSAGE"),
    "export": ("export_service", "USER_EXPORT"),
    "data_flow": ("integration_service", "DATA_FLOW"),
    "backup": ("backup_service", "BACKUP_ARCHIVE"),
    "audit_record": ("security_service", "SECURITY_AUDIT_LOG"),
    "tombstone": ("identity_service", "ACCOUNT_TOMBSTONE"),
    "cache": ("cache_service", "CACHE_ENTRY"),
    "marketing": ("marketing_platform", "MARKETING_PROFILE"),
    "object": ("object_storage", "SUPPORT_ATTACHMENT"),
    "attachment": ("support_platform", "SUPPORT_ATTACHMENT"),
    "telemetry": ("analytics_platform", "PRODUCT_TELEMETRY"),
    "legal_hold": ("legal_service", "LEGAL_HOLD_RECORD"),
    "email_log": ("email_delivery_service", "EMAIL_DELIVERY_LOG"),
}
