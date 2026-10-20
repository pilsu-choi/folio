"""Testset generation: one query per sampled chunk, relevant = that chunk."""

from __future__ import annotations

import os
import random
import re

import httpx

_SENT = re.compile(r"(?<=[.!?。])\s+|\n+")
_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "is", "are", "was", "were", "be", "with", "as", "by", "at", "this", "that", "it"}


def _heuristic_query(text: str) -> str:
    """Pick the longest informative sentence (<= 30 words) and drop stop words."""
    sents = [s.strip() for s in _SENT.split(text) if len(s.split()) >= 3] or [text.strip()]
    best = max(sents, key=lambda s: (min(len(s.split()), 30), -sents.index(s)))
    words = [w for w in best.split()[:30] if w.lower().strip(".,;:()") not in _STOP]
    return " ".join(words[:12]).strip(" .,;:")


def _llm_query(text: str, url: str, model: str | None, api_key: str | None) -> str:
    prompt = ("Write one short search query a user could type to find the passage below. "
              "Do not copy whole sentences. Output only the query.\n\nPassage:\n" + text[:3000])
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    r = httpx.post(url.rstrip("/") + "/chat/completions", headers=headers, timeout=120, json={
        "model": model or os.environ.get("FOLIO_VLM_MODEL", ""), "temperature": 0,
        "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip().strip('"')


def generate(chunks: list[dict], n: int, method: str = "heuristic", llm_url: str | None = None,
             model: str | None = None, seed: int = 0, api_key: str | None = None) -> list[dict]:
    """Sample ``n`` chunks (seeded, deterministic) and build ``{query, relevant_chunk_ids, doc_id}`` items."""
    if method not in ("heuristic", "llm"):
        raise ValueError("method must be 'heuristic' or 'llm'")
    pool = [c for c in chunks if len(c.get("content", "").split()) >= 3]
    chosen = random.Random(seed).sample(pool, min(n, len(pool)))
    out = []
    for c in chosen:
        if method == "llm":
            url = llm_url or os.environ.get("FOLIO_VLM_URL")
            if not url:
                raise ValueError("llm method needs llm_url (or FOLIO_VLM_URL)")
            query = _llm_query(c["content"], url, model, api_key)
        else:
            query = _heuristic_query(c["content"])
        if query:
            out.append({"query": query, "relevant_chunk_ids": [c["chunk_id"]], "doc_id": c["doc_id"]})
    return out
