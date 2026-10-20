"""Claude chat step (``folio.generation.anthropic:chat``) for ``generation.chat`` — same contract as the
OpenAI-compatible ``http`` step: ``fn(messages, **opts) -> str``.

``system`` turns are lifted into the top-level ``system`` field (the Messages API has no system role in
``messages`` at the start). Credentials come from the SDK's usual chain (``ANTHROPIC_API_KEY``, ...).
"""

from __future__ import annotations

import os

from folio.errors import FolioError

DEFAULT_MODEL = "claude-opus-5-5"


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 16000,
    effort: str | None = None,
    fallbacks: bool = True,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 120,
    retries: int = 2,
    client=None,
) -> str:
    """Send ``messages`` to Claude and return the reply text.

    ``fallbacks`` re-runs a safety-declined request on Anthropic's recommended fallback model server-side
    (Claude API only — turn it off behind a proxy or another platform). ``effort`` is low|medium|high|xhigh|max.
    """
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=retries)
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
    params: dict = {"model": model or os.environ.get("FOLIO_LLM_MODEL") or DEFAULT_MODEL,
                    "max_tokens": max_tokens, "messages": turns}
    if system:
        params["system"] = system
    if effort:
        params["output_config"] = {"effort": effort}
    if fallbacks:
        params.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    with client.beta.messages.stream(**params) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        category = getattr(message.stop_details, "category", None)
        raise FolioError(f"generation.anthropic: request declined (category={category})")
    return "".join(b.text for b in message.content if b.type == "text")
