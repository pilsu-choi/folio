"""VLM client (FR-02/05/06/28): one OpenAI-compatible model, the task is chosen by prompt.

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
    "page": ("Convert this document page to Markdown. Use # for headings and '-' for list items. Write tables "
             "as HTML <table> with rowspan/colspan for merged cells. Output only the Markdown."),
    "figure": ("Describe this figure for search. Keep all numbers, labels, legends and axis titles. "
               "Write in the language of the text in the figure. Output a short plain-text description."),
    "table": ("Convert this table to HTML. Use <table>, <tr>, <th>/<td> and rowspan/colspan for merged cells. "
              "Keep every cell text exactly. Output only the HTML."),
    "table_header": ("Below are the first rows of a spreadsheet table, one row per line, cells separated by ' | ', "
                     "row numbers start at 0. How many top rows are header rows (column titles, including multi-level "
                     "and title rows above them) rather than data? Answer only JSON: {\"header_rows\": <int>}."),
}
OFF = (False, None, "off", "none", "")


def make_caller(spec: Any) -> Callable[..., str]:
    """`spec`: callable | "module:function" | {"url", "model", ...} | True (env).
    Returns call(png, task="page", text=None); text-only tasks pass png=None and the input as `text`."""
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

    def call(png: bytes | None, task: str = "page", text: str | None = None) -> str:
        content = [{"type": "text", "text": prompts[task] + (f"\n\n{text}" if text else "")}]
        if png:
            b64 = base64.b64encode(png).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        body = {"model": model, "max_tokens": cfg.get("max_tokens", 4096), "temperature": 0,
                "messages": [{"role": "user", "content": content}]}
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
            var_kw = any(p.kind == p.VAR_KEYWORD for p in ps.values())
            self._task_kw = "task" in ps or var_kw
            self._text_kw = "text" in ps or var_kw
        except (TypeError, ValueError):
            self._task_kw = self._text_kw = False

    @property
    def enabled(self) -> bool:
        return self.caller is not None

    @property
    def text_enabled(self) -> bool:
        """Text-only tasks need a caller that accepts `text=` (custom callers may be image-only)."""
        return self.caller is not None and self._text_kw

    def ask(self, task: str, png: bytes | None, page: int, text: str | None = None) -> str | None:
        """Image task (`png`) or text-only task (`text`, skipped silently when `text_enabled` is False)."""
        if self.caller is None or not (png or (text and self._text_kw)):
            return None
        self.calls += 1
        t0 = time.perf_counter()
        try:
            if text:
                out = self.caller(png, task=task, text=text)
            else:
                out = self.caller(png, task=task) if self._task_kw else self.caller(png)
            if not isinstance(out, str) or not out.strip():
                raise ValueError("empty response")
            log_event("vlm_call", logging.DEBUG, "folio.parse", page=page, task=task, png_bytes=len(png or b""),
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
