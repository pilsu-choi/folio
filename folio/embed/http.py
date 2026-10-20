"""OpenAI-compatible embedding client."""

from __future__ import annotations

import json
import logging
import os
import time

import httpx

from folio.errors import FolioError
from folio.logging import clip, log_event, ms_since, redact_url

LOG = "folio.embed"


def http(
    texts: list[str],
    *,
    url: str | None = None,
    model: str | None = None,
    batch_size: int = 64,
    api_key: str | None = None,
    timeout: float = 60,
    retries: int = 3,
    backoff: float = 0.5,
    transport: httpx.BaseTransport | None = None,
) -> list[list[float]]:
    """POST ``{url}/embeddings`` in batches; order preserved via ``index``; retries 5xx/connection errors."""
    url = (url or os.environ.get("FOLIO_EMBED_URL") or "").rstrip("/")
    if not url:
        raise FolioError("embed.http: no url (set `url` or FOLIO_EMBED_URL)")
    model = model or os.environ.get("FOLIO_EMBED_MODEL") or ""
    api_key = api_key or os.environ.get("FOLIO_EMBED_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    out: list[list[float]] = []
    with httpx.Client(timeout=timeout, headers=headers, transport=transport) as client:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            data = post_json(client, f"{url}/embeddings", {"model": model, "input": batch}, retries, backoff)
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            if len(items) != len(batch):
                raise FolioError(f"embed.http: expected {len(batch)} vectors, got {len(items)}")
            out.extend(d["embedding"] for d in items)
    return out


def post_json(client: httpx.Client, endpoint: str, body: dict, retries: int, backoff: float,
              name: str = "embed.http") -> dict:
    """POST ``body``; retries 5xx/connection errors with exponential backoff, raises 4xx at once."""
    last: Exception | None = None
    ep, size, model = redact_url(endpoint), len(json.dumps(body, default=str)), body.get("model")
    for attempt in range(retries):
        n, t0 = attempt + 1, time.perf_counter()
        log_event("http_request", logging.DEBUG, LOG, client=name, endpoint=ep, bytes=size, model=model, attempt=n, of=retries)
        status = None
        try:
            r = client.post(endpoint, json=body)
            status = r.status_code
            if status < 500:
                try:
                    r.raise_for_status()
                except httpx.HTTPStatusError:
                    log_event("http_failed", logging.ERROR, LOG, client=name, endpoint=ep, status=status,
                              body=clip(r.text, 300), ms=ms_since(t0), attempt=n, of=retries)
                    raise
                log_event("http_response", logging.DEBUG, LOG, client=name, endpoint=ep, status=status,
                          ms=ms_since(t0), resp_bytes=len(r.content), attempt=n)
                return r.json()
            last = FolioError(f"{name}: server error {status}")
        except httpx.TransportError as exc:
            last = exc
        wait = backoff * 2**attempt if attempt < retries - 1 else None
        log_event("http_retry", logging.WARNING, LOG, client=name, endpoint=ep, status=status,
                  error=f"{type(last).__name__}: {clip(last, 200)}", attempt=n, of=retries, next_backoff_s=wait,
                  ms=ms_since(t0))
        if wait is not None:
            time.sleep(wait)
    log_event("http_failed", logging.ERROR, LOG, client=name, endpoint=ep, attempts=retries,
              error=f"{type(last).__name__}: {clip(last, 200)}")
    raise FolioError(f"{name}: failed after {retries} tries: {last}") from last
