"""OpenAI-compatible embedding client."""

from __future__ import annotations

import os
import time

import httpx

from folio.errors import FolioError


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
            data = _post(client, f"{url}/embeddings", {"model": model, "input": batch}, retries, backoff)
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            if len(items) != len(batch):
                raise FolioError(f"embed.http: expected {len(batch)} vectors, got {len(items)}")
            out.extend(d["embedding"] for d in items)
    return out


def _post(client: httpx.Client, endpoint: str, body: dict, retries: int, backoff: float) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = client.post(endpoint, json=body)
            if r.status_code < 500:
                r.raise_for_status()
                return r.json()
            last = FolioError(f"embed.http: server error {r.status_code}")
        except httpx.TransportError as exc:
            last = exc
        if attempt < retries - 1:
            time.sleep(backoff * 2**attempt)
    raise FolioError(f"embed.http: failed after {retries} tries: {last}") from last
