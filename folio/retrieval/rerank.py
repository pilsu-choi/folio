"""Optional rerank via a Cohere/TEI-style ``/rerank`` endpoint."""

from __future__ import annotations

import logging
import os
import time

import httpx

from folio.errors import FolioError
from folio.logging import log_event, ms_since, redact_url
from folio.retrieval.base import Hit


def http_rerank(query: str, hits: list[Hit], url: str | None = None, model: str | None = None, top_n: int | None = None,
                api_key: str | None = None, timeout: float = 60, transport=None) -> list[Hit]:
    """Return ``hits`` reordered by rerank score (score replaced), truncated to ``top_n``."""
    url = (url or os.environ.get("FOLIO_RERANK_URL") or "").rstrip("/")
    if not url:
        raise FolioError("http_rerank: no url (set `url` or FOLIO_RERANK_URL)")
    if not url.endswith("/rerank"):
        url += "/rerank"
    if not hits:
        return []
    texts = [h.content for h in hits]
    body = {"query": query, "documents": texts, "texts": texts}  # Cohere uses documents, TEI uses texts
    if model:
        body["model"] = model
    if top_n:
        body["top_n"] = top_n
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    t0 = time.perf_counter()
    with httpx.Client(timeout=timeout, headers=headers, transport=transport) as client:
        r = client.post(url, json=body)
        log_event("rerank", logging.DEBUG, "folio.retrieval", url=redact_url(url), model=model, docs=len(hits),
                  top_n=top_n, status=r.status_code, ms=ms_since(t0))
        r.raise_for_status()
        data = r.json()
    results = data["results"] if isinstance(data, dict) else data
    scored = sorted(((x.get("relevance_score", x.get("score")), x["index"]) for x in results), key=lambda t: -t[0])
    out = [Hit(hits[i].chunk_id, hits[i].doc_id, s, hits[i].content, hits[i].record) for s, i in scored]
    return out[:top_n] if top_n else out
