"""Optional VLM fallback (FR-06): page image -> markdown -> blocks. Off unless `vlm` is configured."""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Callable
from typing import Any

import httpx

from folio._loader import load_callable

PROMPT = ("Convert this document page to Markdown. Use # for headings, '-' for list items and GitHub "
          "pipe tables for tables. Output only the Markdown.")


def make_caller(spec: Any) -> Callable[[bytes], str]:
    """`spec`: callable | "module:function" | {"url", "model", ...} | True (env FOLIO_VLM_URL/MODEL)."""
    if callable(spec):
        return spec
    if isinstance(spec, str):
        return load_callable(spec)
    cfg = {} if spec is True else dict(spec)
    url = cfg.get("url") or os.environ.get("FOLIO_VLM_URL")
    model = cfg.get("model") or os.environ.get("FOLIO_VLM_MODEL")
    if not url or not model:
        raise ValueError("vlm requires url and model (or FOLIO_VLM_URL / FOLIO_VLM_MODEL)")
    endpoint = url.rstrip("/") if url.rstrip("/").endswith("/chat/completions") else url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}

    def call(png: bytes) -> str:
        b64 = base64.b64encode(png).decode()
        body = {"model": model, "max_tokens": cfg.get("max_tokens", 4096), "temperature": 0,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": cfg.get("prompt", PROMPT)},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}
        r = httpx.post(endpoint, json=body, headers=headers, timeout=cfg.get("timeout", 120))
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    return call


_ROW = re.compile(r"^\s*\|.*\|\s*$")
_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LI = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def markdown_to_blocks(md: str, width: float, height: float) -> list[dict]:
    box = [0.0, 0.0, width, height]
    blocks: list[dict] = []
    buf: list[str] = []
    kind = ""

    def flush():
        nonlocal buf, kind
        if buf:
            if kind == "table":
                rows = [_cells(r) for r in buf if not _SEP.match(r)]
                n = max(len(r) for r in rows)
                rows = [r + [""] * (n - len(r)) for r in rows]
                blocks.append({"type": "table", "text": "\n".join(" | ".join(r) for r in rows), "cells": rows})
            else:
                blocks.append({"type": kind, "text": "\n".join(buf)})
        buf, kind = [], ""

    for line in md.splitlines():
        if not line.strip():
            flush()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            blocks.append({"type": "heading", "text": m.group(2).strip(), "level": len(m.group(1))})
            continue
        k = "table" if _ROW.match(line) else "list" if _LI.match(line) else "paragraph"
        if kind and k != kind and not (kind == "list" and k == "paragraph" and line.startswith(" ")):
            flush()
        kind = kind or k
        buf.append(line.strip() if k == "paragraph" else line)
    flush()
    for b in blocks:
        b["bbox"] = list(box)
        b["text_source"] = "vlm"
    return blocks
