"""Structure server: wraps any StructureBackend behind `POST /v1/analyze` (+ /healthz, /readyz)."""

import json
import os
import threading
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from folio.backends import resolve_backend
from folio.backends.base import PageStructure, Word


def _spec_from_env() -> Any:
    """FOLIO_STRUCTURE_BACKEND (alias | module:Class, default heuristic) + FOLIO_STRUCTURE_DEVICE (cpu | gpu)."""
    spec = os.environ.get("FOLIO_STRUCTURE_BACKEND") or "heuristic"
    device = os.environ.get("FOLIO_STRUCTURE_DEVICE")
    if device and spec != "heuristic":  # the heuristic backend has no device
        return {"fn": spec, "device": device}
    return spec


def create_app(backend_spec: Any = None, warmup: bool = True):
    """Model weights load in a background thread at startup; /readyz is 503 until they are loaded."""
    backend = resolve_backend(backend_spec or _spec_from_env())
    lock = threading.Lock()  # model backends are not assumed thread-safe
    app = FastAPI(title="folio structure server")
    app.state.backend = backend
    state = {"ready": not (warmup and hasattr(backend, "warmup")), "error": None}

    def _load() -> None:
        try:
            with lock:
                backend.warmup()
            state["ready"] = True
        except Exception as exc:  # reported by /readyz; the pod stays unready
            state["error"] = f"{type(exc).__name__}: {exc}"

    if not state["ready"]:
        threading.Thread(target=_load, name="structure-warmup", daemon=True).start()

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        info = {"backend": type(backend).__name__, "device": getattr(backend, "device", None)}
        if state["ready"]:
            return {"status": "ready", **info}
        return JSONResponse({"status": "loading" if not state["error"] else "error",
                             "error": state["error"], **info}, status_code=503)

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
