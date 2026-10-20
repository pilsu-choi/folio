"""Structure server: wraps any StructureBackend behind `POST /v1/analyze` (+ /healthz, /readyz)."""

import json
import os
import threading
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from folio.backends import resolve_backend
from folio.backends.base import PageStructure, Word


def create_app(backend_spec: Any = None):
    """`backend_spec` default: env FOLIO_STRUCTURE_BACKEND (alias | module:Class), else heuristic."""
    spec = backend_spec or os.environ.get("FOLIO_STRUCTURE_BACKEND") or "heuristic"
    backend = resolve_backend(spec)
    lock = threading.Lock()  # model backends are not assumed thread-safe
    app = FastAPI(title="folio structure server")
    app.state.backend = backend

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        return {"status": "ready", "backend": type(backend).__name__}

    @app.post("/v1/analyze", response_model=PageStructure)
    def analyze(image: UploadFile = File(...), words: str | None = Form(None)):
        try:
            ws = None if words is None else [Word.model_validate(w) for w in json.loads(words)]
        except Exception as exc:
            raise HTTPException(422, f"invalid words: {exc}") from exc
        data = image.file.read()
        try:
            with lock:
                return backend.analyze(data, ws)
        except Exception as exc:
            return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)

    return app


def serve(host: str = "0.0.0.0", port: int = 9000) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)
