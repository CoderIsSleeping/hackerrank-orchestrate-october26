"""Thin LLM client: structured JSON output, on-disk cache, usage accounting, two optional providers.

- Provider: Anthropic Claude when ANTHROPIC_API_KEY is set, else Google Gemini when GEMINI_API_KEY is
  set, else none (the pipeline runs offline). LLM_PROVIDER=anthropic|gemini|none forces a choice.
- Both providers get the same system prompt, content and JSON schema, so callers are provider-agnostic.
- Every call is cached by a hash of (provider, model, request), so re-runs are deterministic and free.
- Usage (calls, input/output tokens) is accumulated per model for evaluation/usage_report.md.
- Keys are read from the environment or a local uncommitted .env, and never printed or logged.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

from config import CACHE_DIR, GEMINI_MODEL, MODEL, REPO_ROOT

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:          # python-dotenv is optional; the environment variable alone is enough
    pass

# Opus 5.5 can decline a request via safety classifiers; "default" lets the API re-run a declined
# request on Anthropic's recommended fallback model instead of failing the case.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(RuntimeError):
    pass


class Usage:
    def __init__(self):
        # "calls"/"*_tokens" count every model response the run used, whether it was fetched now or
        # replayed from the cache (cached responses were paid for once, when first produced);
        # "live_calls" counts only requests actually sent during this run.
        self.by_model = defaultdict(lambda: {"calls": 0, "live_calls": 0, "input_tokens": 0, "output_tokens": 0})

    def add(self, model: str, inp: int, out: int, cached: bool) -> None:
        row = self.by_model[model]
        row["calls"] += 1
        row["live_calls"] += 0 if cached else 1
        row["input_tokens"] += inp
        row["output_tokens"] += out

    def as_dict(self) -> dict:
        return {m: dict(v) for m, v in self.by_model.items()}


USAGE = Usage()
_client = None
_disabled_reason: str | None = None   # set after an unrecoverable API error; the run continues offline


def provider() -> str | None:
    forced = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if forced in ("none", "offline"):
        return None
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    if forced == "anthropic":
        return "anthropic" if has_anthropic else None
    if forced == "gemini":
        return "gemini" if has_gemini else None
    return "anthropic" if has_anthropic else ("gemini" if has_gemini else None)


def model_name() -> str:
    return GEMINI_MODEL if provider() == "gemini" else MODEL


def available() -> bool:
    if _disabled_reason:
        return False
    return provider() is not None


def disable(reason: str) -> None:
    """Switch the rest of the run to offline mode (never includes the key in the reason)."""
    global _disabled_reason
    _disabled_reason = reason


def disabled_reason() -> str | None:
    return _disabled_reason


def _get_client():
    global _client
    if _client is None:
        if not available():
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        import anthropic
        # The SDK retries 408/409/429/5xx with exponential backoff; 4 retries rides out short quota spikes.
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def file_block(path: Path) -> dict:
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    if path.suffix.lower() == ".pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def call_json(system: str, content: list, schema: dict, *, effort: str = "low",
              max_tokens: int = 16000, cache_key_extra: str = "") -> dict:
    """One structured-output call. Returns the parsed JSON object (validated by the API schema)."""
    prov = provider()
    request = {"model": model_name(), "system": system, "content": content, "schema": schema, "effort": effort}
    if prov == "gemini":
        request["provider"] = "gemini"      # Anthropic cache keys stay unchanged
    key = hashlib.sha256((json.dumps(request, sort_keys=True) + cache_key_extra).encode()).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        USAGE.add(cached["model"], cached["input_tokens"], cached["output_tokens"], cached=True)
        return cached["output"]

    if prov == "gemini":
        model, output, inp, out = _call_gemini(system, content, schema)
        USAGE.add(model, inp, out, cached=False)
        path.write_text(json.dumps({"model": model, "input_tokens": inp, "output_tokens": out, "output": output},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        return output

    import anthropic
    client = _get_client()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        disable("API key rejected")
        raise LLMUnavailable("API key rejected") from e
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as e:
        # The SDK already retried with backoff; give up on Claude for this run rather than stall.
        disable(f"API unreachable after retries ({type(e).__name__})")
        raise LLMUnavailable(type(e).__name__) from e
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined the request: {getattr(response.stop_details, 'category', None)}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude output truncated at max_tokens")
    text = next(b.text for b in response.content if b.type == "text")
    output = json.loads(text)
    USAGE.add(response.model, response.usage.input_tokens, response.usage.output_tokens, cached=False)
    path.write_text(json.dumps({"model": response.model, "input_tokens": response.usage.input_tokens,
                                "output_tokens": response.usage.output_tokens, "output": output},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return output


# ------------------------------------------------------------------ Gemini

_gemini = None
# Free-tier quotas are small (requests per minute and per day). 429s carry a suggested retry delay;
# 503 means the model is overloaded. Both are retried a few times with backoff before giving up.
GEMINI_MAX_ATTEMPTS = 5
RETRIES: dict[str, int] = defaultdict(int)     # e.g. {"429": 3, "503": 1}: reported, never hidden


def _gemini_client():
    global _gemini
    if _gemini is None:
        from google import genai
        _gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _gemini


def _gemini_parts(content: list) -> list:
    from google.genai import types
    parts = []
    for block in content:
        if block["type"] == "text":
            parts.append(types.Part.from_text(text=block["text"]))
        else:   # image / document blocks built by file_block()
            src = block["source"]
            parts.append(types.Part.from_bytes(data=base64.b64decode(src["data"]), mime_type=src["media_type"]))
    return parts


def _retry_delay(err, attempt: int) -> float:
    m = re.search(r"retry in ([0-9.]+)s|'retryDelay': '([0-9.]+)s'", str(err))
    if m:
        return float(m.group(1) or m.group(2)) + 1.0
    return min(60.0, 4.0 * 2 ** attempt)


def _call_gemini(system: str, content: list, schema: dict) -> tuple[str, dict, int, int]:
    from google.genai import errors, types
    client = _gemini_client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_json_schema=schema,
        temperature=0.0,              # transcription and classification: no creativity wanted
        max_output_tokens=16384,
    )
    for attempt in range(GEMINI_MAX_ATTEMPTS):
        try:
            r = client.models.generate_content(model=GEMINI_MODEL, contents=_gemini_parts(content), config=config)
            break
        except errors.ClientError as e:
            if e.code in (401, 403):
                disable("Gemini API key rejected")
                raise LLMUnavailable("Gemini API key rejected") from e
            if e.code == 429 and attempt + 1 < GEMINI_MAX_ATTEMPTS and "PerDay" not in str(e):
                RETRIES["429"] += 1
                time.sleep(_retry_delay(e, attempt))
                continue
            if e.code == 429:
                quota = re.findall(r"'quotaId': '([^']+)'.*?'quotaValue': '([^']+)'", str(e), re.S)
                detail = ", ".join(f"{q} limit {v}" for q, v in quota) or "no quota detail returned"
                disable(f"Gemini quota exhausted ({detail})")
                raise LLMUnavailable("Gemini quota exhausted") from e
            raise
        except errors.ServerError as e:
            if attempt + 1 < GEMINI_MAX_ATTEMPTS:
                RETRIES[str(e.code)] += 1
                time.sleep(_retry_delay(e, attempt))
                continue
            raise
    finish = str(r.candidates[0].finish_reason) if r.candidates else "NO_CANDIDATE"
    if "MAX_TOKENS" in finish:
        raise RuntimeError("Gemini output truncated at max_output_tokens")
    if not r.text:
        raise RuntimeError(f"Gemini returned no text (finish_reason={finish})")
    output = json.loads(r.text)
    u = r.usage_metadata
    out_tokens = (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)   # thinking is billed as output
    return r.model_version or GEMINI_MODEL, output, u.prompt_token_count or 0, out_tokens
