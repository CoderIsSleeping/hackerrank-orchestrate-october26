# Usage report - final full-dataset run

Run: `python code/main.py` on `dataset/deletion_requests.csv` (518 requests) -> `output.csv`, 16.2 s wall time. LLM provider: none (offline).

Token counts come from `response.usage` of every Claude response the run used (`code/llm.py`).
Responses are cached on disk by request hash, so a re-run replays them: *calls* counts every
response used by the run, *live calls* only those sent during this particular execution.
Prices: claude-opus-5-5 at $4.00 / 1M input and $20.00 / 1M output tokens; Gemini calls were made on the free tier and billed $0 (Gemini output tokens include thinking tokens).

| Provider | Model | Calls | Live calls | Input tokens | Output tokens | Total tokens | Est. cost (USD) |
|---|---|---|---|---|---|---|---|
| **Overall** | | **0** | **0** | **0** | **0** | **0** | **0.0000** |

| Per request (÷ requests) | Value |
|---|---|
| Model calls | 0.000 |
| Tokens | 0.0 |
| Estimated cost (USD) | 0.00000 |

Why so few calls: evidence is parsed once per run and shared by all requests; Claude is only used
for judgement that code cannot make - reading the 15 PDFs/screenshots (one call per file) and
classifying the intent of each *unique* request text (batched, 40 per call). Identity resolution,
state reconstruction, policy, ordering and validation are deterministic code with zero tokens.
