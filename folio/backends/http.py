"""Client for the structure server (`folio structure-server`)."""

from __future__ import annotations

import json
import logging
import os
import time

import httpx

from folio.backends.base import PageStructure, Word
from folio.logging import log_event, ms_since, redact_url


class HttpBackend:
    def __init__(self, url: str | None = None, timeout: float = 120.0, client: httpx.Client | None = None):
        self.url = (url or os.environ.get("FOLIO_STRUCTURE_URL") or "").rstrip("/")
        if not self.url:
            raise ValueError("HttpBackend needs `url` or env FOLIO_STRUCTURE_URL")
        self.timeout = timeout
        self._client = client

    def analyze(self, page_image: bytes, words: list[Word] | None) -> PageStructure:
        files = {"image": ("page.png", page_image, "image/png")}
        if words is not None:
            files["words"] = (None, json.dumps([w.model_dump() for w in words]), "application/json")
        t0 = time.perf_counter()
        post = self._client.post if self._client else httpx.post
        r = post(f"{self.url}/v1/analyze", files=files, timeout=self.timeout)
        log_event("structure_call", logging.DEBUG, "folio.backends", url=redact_url(self.url), image_bytes=len(page_image),
                  words=None if words is None else len(words), status=r.status_code, ms=ms_since(t0))
        r.raise_for_status()
        return PageStructure.model_validate(r.json())
