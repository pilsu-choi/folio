"""Grounded, multi-turn answers over the pipeline's search results.

Stateless: the caller sends the whole conversation (``user``/``assistant`` turns, last one ``user``) every time.
A follow-up is first rewritten into a standalone search query, then answered from numbered passages cited as ``[n]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from folio.chunk.tokens import count_tokens
from folio.contracts import PipelineConfig
from folio.errors import ConfigError, GenerationError
from folio.retrieval.base import Hit

SYSTEM = ("Answer the user's question using only the numbered passages below. Cite the passages you use as [n] "
          "right after the sentence they support. If the passages do not contain the answer, say so. "
          "Answer in the language of the question.")
REWRITE = ("Rewrite the last user question as one standalone search query, resolving references to the "
           "conversation. Keep names, numbers and terms. Output only the query, in the language of the question.")
ROLES = ("user", "assistant")
_CITE = re.compile(r"\[(\d+)\]")


@dataclass
class Answer:
    answer: str
    query: str  # what was searched (the rewritten follow-up, or the question itself)
    citations: list[dict] = field(default_factory=list)  # {n, chunk_id, doc_id}, n = passage number in `hits`
    hits: list[Hit] = field(default_factory=list)  # passages given to the LLM, in [n] order


def check_messages(messages: list[dict]) -> list[dict]:
    """Return ``[{role, content}]``; raise ``ValueError`` unless roles are user/assistant and the last one is user."""
    if not messages:
        raise ValueError("messages must not be empty")
    out = []
    for i, m in enumerate(messages):
        role, content = m.get("role"), m.get("content")
        if role not in ROLES:
            raise ValueError(f"messages[{i}].role must be one of {ROLES}, got {role!r}")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"messages[{i}].content must be a non-empty string")
        out.append({"role": role, "content": content})
    if out[-1]["role"] != "user":
        raise ValueError("the last message must be from the user")
    return out


def _history(messages: list[dict], budget: int) -> list[dict]:
    """Newest turns within ``budget`` tokens, old ``[n]`` markers stripped (they point at earlier passages),
    starting with a user turn."""
    out: list[dict] = []
    used = 0
    for m in reversed(messages):
        content = _CITE.sub("", m["content"]).strip() if m["role"] == "assistant" else m["content"]
        if not content:
            continue
        used += count_tokens(content)
        if used > budget:
            break
        out.append({"role": m["role"], "content": content})
    out.reverse()
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _passages(hits: list[Hit], budget: int) -> list[Hit]:
    out, used = [], 0
    for h in hits:
        used += count_tokens(h.content)
        if out and used > budget:
            break
        out.append(h)
    return out


def _label(h: Hit) -> str:
    rec = h.record or {}
    parts = [" > ".join(rec.get("section_path") or []), f"p.{rec['page']}" if rec.get("page") else ""]
    return " · ".join(p for p in parts if p)


def _context(hits: list[Hit]) -> str:
    if not hits:
        return "(no passages found)"
    return "\n\n".join(f"[{i}] {_label(h)}".rstrip() + f"\n{h.content}" for i, h in enumerate(hits, 1))


def _call(chat, messages: list[dict]) -> str:
    try:
        return chat(messages).strip()
    except Exception as exc:
        raise GenerationError(f"generation: chat step failed: {type(exc).__name__}: {exc}") from exc


def answer(cfg: PipelineConfig, messages: list[dict], k: int | None = None, filters: dict | None = None,
           mode: str | None = None) -> Answer:
    """Search and answer the last user turn. Raises ``ConfigError`` without ``generation:``, ``ValueError`` for bad
    messages/filters and ``GenerationError`` when the chat step fails."""
    from folio.pipeline import _fn, _prepare_path, search

    gen = cfg.generation
    if gen is None:
        raise ConfigError(f"pipeline {cfg.name!r} has no generation: section")
    messages = check_messages(messages)
    _prepare_path(cfg)
    chat = _fn(gen.chat)
    question = messages[-1]["content"]
    history = _history(messages[:-1], gen.max_history_tokens)
    query = question
    if len(messages) > 1 and gen.rewrite:
        # over-budget history still gets the previous user turn, so a follow-up is never searched verbatim
        context = history or [next(m for m in reversed(messages[:-1]) if m["role"] == "user")]
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in context)
        prompt = f"{REWRITE}\n\nConversation:\n{transcript}\n\nLast question: {question}"
        query = _call(chat, [{"role": "user", "content": prompt}]).strip('"') or question
    hits = _passages(search(cfg, query, k=k, filters=filters, mode=mode), gen.max_context_tokens)
    system = f"{gen.system or SYSTEM}\n\nPassages:\n{_context(hits)}"
    text = _call(chat, [{"role": "system", "content": system}, *history, {"role": "user", "content": question}])
    cited = sorted({n for n in map(int, _CITE.findall(text)) if 1 <= n <= len(hits)})
    return Answer(text, query, [{"n": n, "chunk_id": hits[n - 1].chunk_id, "doc_id": hits[n - 1].doc_id}
                                for n in cited], hits)
