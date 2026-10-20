"""OpenAI-compatible text chat client (``/chat/completions``)."""

from __future__ import annotations

import os

import httpx

from folio.embed.http import post_json
from folio.errors import FolioError


def http(
    messages: list[dict],
    *,
    url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    temperature: float = 0,
    max_tokens: int = 1024,
    timeout: float = 120,
    retries: int = 3,
    backoff: float = 0.5,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """POST ``messages`` to ``{url}/chat/completions`` and return the reply text; retries 5xx/connection errors."""
    url = (url or os.environ.get("FOLIO_LLM_URL") or "").rstrip("/")
    if not url:
        raise FolioError("generation.http: no url (set `url` or FOLIO_LLM_URL)")
    endpoint = url if url.endswith("/chat/completions") else url + "/chat/completions"
    model = model or os.environ.get("FOLIO_LLM_MODEL") or ""
    api_key = api_key or os.environ.get("FOLIO_LLM_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    with httpx.Client(timeout=timeout, headers=headers, transport=transport) as client:
        data = post_json(client, endpoint, body, retries, backoff, name="generation.http")
    return data["choices"][0]["message"]["content"] or ""
