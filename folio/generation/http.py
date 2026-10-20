"""OpenAI-compatible text chat client (``/chat/completions``)."""

from __future__ import annotations

import logging
import os
import time

import httpx

from folio.embed.http import post_json
from folio.errors import FolioError
from folio.logging import clip, debug_enabled, log_event, ms_since


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
    extra_body: dict | None = None,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """POST ``messages`` to ``{url}/chat/completions`` and return the reply text; retries 5xx/connection errors.
    ``extra_body`` is merged into the request (e.g. vLLM ``{"chat_template_kwargs": {"enable_thinking": False}}``)."""
    url = (url or os.environ.get("FOLIO_LLM_URL") or "").rstrip("/")
    if not url:
        raise FolioError("generation.http: no url (set `url` or FOLIO_LLM_URL)")
    endpoint = url if url.endswith("/chat/completions") else url + "/chat/completions"
    model = model or os.environ.get("FOLIO_LLM_MODEL") or ""
    api_key = api_key or os.environ.get("FOLIO_LLM_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, **(extra_body or {})}
    t0 = time.perf_counter()
    with httpx.Client(timeout=timeout, headers=headers, transport=transport) as client:
        data = post_json(client, endpoint, body, retries, backoff, name="generation.http")
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        log_event("llm_bad_response", logging.ERROR, "folio.generation", model=model, body=clip(data, 500))
        raise FolioError(f"generation.http: response has no `choices` (model={model!r}): {clip(data, 200)}")
    reply = choices[0].get("message", {}).get("content") or ""
    finish = choices[0].get("finish_reason")
    if finish == "length":
        log_event("llm_truncated", logging.WARNING, "folio.generation", model=model, max_tokens=max_tokens,
                  reply_chars=len(reply), usage=data.get("usage"))
    if debug_enabled("folio.generation"):
        log_event("llm_reply", logging.DEBUG, "folio.generation", model=model, finish_reason=finish,
                  usage=data.get("usage"), ms=ms_since(t0), reply_chars=len(reply), messages=len(messages),
                  reply=clip(reply))
    return reply
