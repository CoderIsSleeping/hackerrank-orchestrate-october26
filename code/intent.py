"""Does the request text actually authorize deletion?

Primary: Claude classifies each unique request text (multilingual) into a request type.
Cross-check / offline fallback: a small multilingual keyword rule. Disagreements are recorded so
the evaluation report can show them.
"""
from __future__ import annotations

import re

import llm

REQUEST_TYPES = ["DELETION", "ACCESS", "EXPORT", "CORRECTION", "MARKETING_OPT_OUT", "OTHER"]
BATCH_SIZE = 40   # unique texts per call: small enough to keep each answer short and auditable

SYSTEM_PROMPT = """You classify privacy requests sent to an online shop.
For each request decide what the person is actually asking for:
- DELETION: erase / close / permanently remove the account or its personal data.
- ACCESS: see or receive a copy of what data is held (without deleting it).
- EXPORT: download / port their data.
- CORRECTION: fix or update personal details.
- MARKETING_OPT_OUT: stop marketing or newsletters while keeping the account.
- OTHER: anything else.
Requests may be in any language. Judge the request the person makes, not words that merely appear:
"do not delete my account, just send me a copy" is ACCESS. A request that asks for deletion and also
asks for confirmation, verification or a status check is still DELETION.
authorizes_deletion is true only for DELETION."""

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "request_type": {"type": "string", "enum": REQUEST_TYPES},
                    "authorizes_deletion": {"type": "boolean"},
                },
                "required": ["id", "request_type", "authorizes_deletion"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

# Deletion verbs in the languages seen in the data (EN, ES, PT, FR, DE, IT, JA, KO, ZH, NL, PL, TR).
_DELETE = re.compile(
    r"delet|eras|remov|close (my|this|the) account|wipe|purge|suppress|profile is gone|cannot identify me|"
    r"elimin|borr|exclu|apag|supprim|effac|lösch|cancell|verwijder|usuń|usun|\bsil|"
    r"削除|消去|삭제|删除|注销|註銷", re.I)
# Non-deletion patterns are deliberately specific: deletion requests routinely say things like
# "apply the correct treatment" or "handle any copies", which must not flip the label.
_NON_DELETION = re.compile(
    # negated deletion (EN / ES / PT / FR)
    r"(do not|don't|not)\s+(want to\s+|wish to\s+)?(delete|erase|remove|close)|instead of delet|rather than delet|"
    r"no (deseo|quiero) (eliminar|borrar)|não (quero|desejo) (excluir|apagar)|ne pas (supprimer|effacer)|"
    r"gardez mon compte|keep my account|mantener mi cuenta|manter minha conta|"
    # access / export
    r"(send|give|provide|need|want|get)( me)? a copy of|send me my data|copia de mis datos|cópia dos meus dados|copie de mes données|"
    r"download my data|export my data|data portability|what (personal )?data you (hold|have)|"
    # correction
    r"correct my|update my (name|email|address|phone)|change my (name|email|address|phone)|"
    r"corregir|corrigir|corriger|correct it|misspelled|resetting the login|reset the login|"
    # explicit statements that this is not a deletion request
    r"not asking for deletion|not a request to (erase|delete|remove)|"
    # marketing opt-out
    r"unsubscribe|newsletter|stop (sending|marketing)|promotional emails|marketing (emails|lists)|opt out of marketing", re.I)


def rule_label(text: str) -> str:
    if _NON_DELETION.search(text):
        return "OTHER"
    return "DELETION" if _DELETE.search(text) else "OTHER"


def classify(texts: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Return {text: {"request_type", "authorizes_deletion", "source"}} and a list of notes."""
    unique = sorted(set(texts))
    out: dict[str, dict] = {}
    notes: list[str] = []
    if llm.available():
        for start in range(0, len(unique), BATCH_SIZE):
            batch = unique[start:start + BATCH_SIZE]
            items = "\n".join(f'<request id="t{i}">{t}</request>' for i, t in enumerate(batch))
            if not llm.available():
                break
            try:
                result = llm.call_json(SYSTEM_PROMPT, [{"type": "text", "text": items}], SCHEMA, effort="low")
            except Exception as e:     # fall back to the keyword rule for this batch
                notes.append(f"intent: Claude batch failed ({type(e).__name__}); keyword rule used")
                continue
            by_id = {r["id"]: r for r in result["results"]}
            for i, t in enumerate(batch):
                r = by_id.get(f"t{i}")
                if r is None:
                    notes.append(f"intent: model skipped a request, rule used: {t[:60]}")
                    continue
                out[t] = {"request_type": r["request_type"],
                          "authorizes_deletion": r["request_type"] == "DELETION", "source": "claude"}
    for t in unique:
        rl = rule_label(t)
        if t not in out:
            out[t] = {"request_type": rl, "authorizes_deletion": rl == "DELETION", "source": "rule"}
        elif (rl == "DELETION") != out[t]["authorizes_deletion"]:
            notes.append(f"intent disagreement (claude={out[t]['request_type']}, rule={rl}): {t[:90]}")
    return out, notes
