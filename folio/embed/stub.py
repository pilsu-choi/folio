"""Deterministic OpenAI-compatible embedding server for dev/tests (``folio embed-stub``).

Vectors are L2-normalised sums of per-token hashed pseudo-random vectors, so texts sharing words are close.
Dimension from env ``FOLIO_EMBED_STUB_DIM`` (default 384).
"""

from __future__ import annotations

import hashlib
import math
import os
import re

import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel

_WORD = re.compile(r"\w+", re.UNICODE)


def embed_text(text: str, dim: int) -> list[float]:
    vec = np.zeros(dim)
    for tok in _WORD.findall(text.lower()) or [text]:
        seed = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:8], "big")
        vec += np.random.default_rng(seed).standard_normal(dim)
    norm = math.sqrt(float((vec**2).sum())) or 1.0
    return (vec / norm).tolist()


class _Req(BaseModel):
    input: str | list[str]
    model: str = "folio-stub"


def create_app() -> FastAPI:
    app = FastAPI(title="folio embed stub")
    dim = lambda: int(os.environ.get("FOLIO_EMBED_STUB_DIM", "384"))

    @app.post("/v1/embeddings")
    def embeddings(req: _Req):
        texts = [req.input] if isinstance(req.input, str) else req.input
        d = dim()
        data = [{"object": "embedding", "index": i, "embedding": embed_text(t, d)} for i, t in enumerate(texts)]
        n = sum(len(t.split()) for t in texts)
        return {"object": "list", "data": data, "model": req.model, "usage": {"prompt_tokens": n, "total_tokens": n}}

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": "folio-stub", "object": "model", "owned_by": "folio"}]}

    @app.get("/healthz")
    @app.get("/readyz")
    def health():
        return {"status": "ok"}

    return app


def serve(host: str = "0.0.0.0", port: int = 8001) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)
