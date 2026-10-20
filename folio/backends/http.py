"""Client for the structure server (`folio structure-server`)."""

from __future__ import annotations

import json
import os

import httpx

from folio.backends.base import PageStructure, Word


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
        post = self._client.post if self._client else httpx.post
        r = post(f"{self.url}/v1/analyze", files=files, timeout=self.timeout)
        r.raise_for_status()
        return PageStructure.model_validate(r.json())
