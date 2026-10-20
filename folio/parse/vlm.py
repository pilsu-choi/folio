"""VLM client (FR-02/05/06): one OpenAI-compatible model, the task is chosen by prompt.

`Session` wraps the configured caller, counts requests and records per-task fallbacks. It never raises:
`ask()` returns None when the VLM is disabled or the request failed (the failure is recorded in `fallbacks`).
"""

from __future__ import annotations

import base64
import inspect
import io
import logging
import os
import time
from collections.abc import Callable
from typing import Any

import httpx
from PIL import Image

from folio._loader import load_callable
from folio.logging import clip, log_event, ms_since

PROMPTS = {
    "page": ("Convert this document page to Markdown. Use # for headings, '-' for list items and GitHub "
             "pipe tables for tables. Output only the Markdown."),
    "figure": ("Describe this figure for search. Keep all numbers, labels, legends and axis titles. "
               "Output a short plain-text description."),
    "table": ("Convert this table to a GitHub pipe table in Markdown. Keep every cell text exactly. "
              "Output only the table."),
}
OFF = (False, None, "off", "none", "")


def make_caller(spec: Any) -> Callable[..., str]:
    """`spec`: callable | "module:function" | {"url", "model", ...} | True (env). Returns call(png, task="page")."""
    if callable(spec):
        return spec
    if isinstance(spec, str) and ":" in spec:
        return load_callable(spec)
    cfg = {} if spec in (True, "auto") else dict(spec)
    url = cfg.get("url") or os.environ.get("FOLIO_VLM_URL")
    model = cfg.get("model") or os.environ.get("FOLIO_VLM_MODEL") or "folio-vlm"
    if not url:
        raise ValueError("vlm requires url (or FOLIO_VLM_URL)")
    url = url.rstrip("/")
    endpoint = url if url.endswith("/chat/completions") else url + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}
    prompts = {**PROMPTS, **cfg.get("prompts", {})}
    client = cfg.get("client")  # injectable httpx.Client (tests)

    def call(png: bytes, task: str = "page") -> str:
        b64 = base64.b64encode(png).decode()
        body = {"model": model, "max_tokens": cfg.get("max_tokens", 4096), "temperature": 0,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompts[task]},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}
        post = client.post if client is not None else httpx.post
        kw = {} if client is not None else {"timeout": cfg.get("timeout", 120)}
        r = post(endpoint, json=body, headers=headers, **kw)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    return call


def resolve(spec: Any) -> tuple[Callable[..., str] | None, dict]:
    """vlm option -> (caller | None, meta["vlm"]). "auto": enabled iff FOLIO_VLM_URL is set."""
    if spec in OFF:
        return None, {"enabled": False, "reason": "disabled by option"}
    if spec == "auto":
        if not os.environ.get("FOLIO_VLM_URL"):
            return None, {"enabled": False, "reason": "FOLIO_VLM_URL not set"}
        return make_caller(True), {"enabled": True, "model": os.environ.get("FOLIO_VLM_MODEL") or "folio-vlm"}
    info = {"enabled": True}
    if isinstance(spec, dict):
        info["model"] = spec.get("model") or os.environ.get("FOLIO_VLM_MODEL") or "folio-vlm"
    return make_caller(spec), info


def to_png(blob: bytes) -> bytes | None:
    """Any image bytes -> PNG (None when undecodable)."""
    try:
        im = Image.open(io.BytesIO(blob))
        im.load()
        if im.mode not in ("L", "RGB"):
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()
    except Exception:
        return None


class Session:
    def __init__(self, caller: Callable[..., str] | None):
        self.caller = caller
        self.calls = 0
        self.fallbacks: list[dict] = []
        try:
            ps = inspect.signature(caller).parameters if caller else {}
            self._task_kw = "task" in ps or any(p.kind == p.VAR_KEYWORD for p in ps.values())
        except (TypeError, ValueError):
            self._task_kw = False

    @property
    def enabled(self) -> bool:
        return self.caller is not None

    def ask(self, task: str, png: bytes | None, page: int) -> str | None:
        if self.caller is None or not png:
            return None
        self.calls += 1
        t0 = time.perf_counter()
        try:
            out = self.caller(png, task=task) if self._task_kw else self.caller(png)
            if not isinstance(out, str) or not out.strip():
                raise ValueError("empty response")
            log_event("vlm_call", logging.DEBUG, "folio.parse", page=page, task=task, png_bytes=len(png),
                      ms=ms_since(t0), reply_chars=len(out), reply=clip(out, 200))
            return out
        except Exception as exc:
            log_event("vlm_fallback", logging.WARNING, "folio.parse", page=page, task=task, ms=ms_since(t0),
                      error=f"{type(exc).__name__}: {clip(exc, 300)}")
            self.fallbacks.append({"page": page, "task": task, "error": f"vlm: {type(exc).__name__}: {exc}"})
            return None

    def note(self, page: int, task: str, error: str) -> None:
        log_event("vlm_fallback", logging.WARNING, "folio.parse", page=page, task=task, error=clip(error, 300))
        self.fallbacks.append({"page": page, "task": task, "error": f"vlm: {error}"})
