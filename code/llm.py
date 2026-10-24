"""Thin Claude client: structured JSON output, on-disk cache, usage accounting.

- Every call is cached by a hash of its full request, so re-runs are deterministic and free.
- Usage (calls, input/output tokens) is accumulated per model for evaluation/usage_report.md.
- The key is read from the environment (ANTHROPIC_API_KEY, or a local uncommitted .env).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

from config import CACHE_DIR, MODEL, REPO_ROOT

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


def available() -> bool:
    if _disabled_reason:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


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
    request = {"model": MODEL, "system": system, "content": content, "schema": schema, "effort": effort}
    key = hashlib.sha256((json.dumps(request, sort_keys=True) + cache_key_extra).encode()).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        USAGE.add(cached["model"], cached["input_tokens"], cached["output_tokens"], cached=True)
        return cached["output"]

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
