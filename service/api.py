"""FastAPI ingest (FR-14), search (FR-30) and chat (FR-31) API."""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from service import settings
from service.jobs import JobStore


@asynccontextmanager
async def _lifespan(_app):
    try:
        JobStore().create_all()
    except Exception:  # readiness will report it
        pass
    yield


app = FastAPI(title="folio", lifespan=_lifespan)


def _storage():
    from folio.storage import open_storage

    st = open_storage(settings.upload_storage_uri())
    st.ensure()
    return st


@app.post("/v1/ingest", status_code=202)
async def ingest(file: UploadFile = File(...), pipeline: str = Form(None)):
    from folio.pipeline import load_pipeline
    from service.worker import ingest as ingest_task

    pipeline = pipeline or settings.default_pipeline()
    try:
        load_pipeline(pipeline, settings.config_dir())
    except Exception as exc:
        raise HTTPException(400, f"unknown pipeline {pipeline!r}: {exc}") from exc
    filename = re.sub(r"[^\w.\-가-힣]", "_", os.path.basename(file.filename or "upload.bin"))
    store = JobStore()
    store.create_all()
    job_id = store.create(pipeline, "", filename)
    st = _storage()
    key = f"uploads/{job_id}/{filename}"
    st.put_bytes(key, await file.read())
    store.update(job_id, source_uri=st.uri(key))
    ingest_task.delay(job_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str):
    job = JobStore().get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return {
        "job_id": job["id"], "pipeline": job["pipeline"], "status": job["status"],
        "stages": job["stages"], "doc_id": job["doc_id"], "error": job["error"],
        "created_at": job["created_at"].isoformat(), "updated_at": job["updated_at"].isoformat(),
    }


@app.delete("/v1/documents/{doc_id}")
def delete_document(doc_id: str, pipeline: str | None = None):
    from folio.pipeline import delete_document as delete
    from folio.pipeline import load_pipeline

    cfg = load_pipeline(pipeline or settings.default_pipeline(), settings.config_dir())
    delete(cfg, doc_id)
    return {"doc_id": doc_id, "deleted": True}


class _Retrieve(BaseModel):
    pipeline: str | None = None
    k: int | None = Field(default=None, ge=1, le=100)
    mode: str | None = None
    filters: dict = Field(default_factory=dict)


class SearchRequest(_Retrieve):
    query: str = Field(min_length=1)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]  # no client "system": the grounding prompt stays server-side
    content: str = Field(min_length=1, max_length=20000)


class ChatRequest(_Retrieve):
    messages: list[ChatMessage] = Field(min_length=1, max_length=50, description="full history, last turn = user")


def _pipeline(name: str | None):
    from folio.pipeline import load_pipeline

    name = name or settings.default_pipeline()
    try:
        return name, load_pipeline(name, settings.config_dir())
    except Exception as exc:
        raise HTTPException(400, f"unknown pipeline {name!r}: {exc}") from exc


def _hit(h) -> dict:
    return {"chunk_id": h.chunk_id, "doc_id": h.doc_id, "score": h.score, "content": h.content, "record": h.record}


@app.post("/v1/search")
def search(req: SearchRequest):
    from folio.errors import ConfigError
    from folio.pipeline import search as run_search

    pipeline, cfg = _pipeline(req.pipeline)
    try:
        hits = run_search(cfg, req.query, k=req.k, filters=req.filters, mode=req.mode)
    except (ValueError, ConfigError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"pipeline": pipeline, "hits": [_hit(h) for h in hits]}


@app.post("/v1/chat")
def chat(req: ChatRequest):
    """Stateless multi-turn RAG: the App sends the whole conversation each time; folio stores none of it."""
    from folio.errors import ConfigError, GenerationError
    from folio.generation import answer

    pipeline, cfg = _pipeline(req.pipeline)
    try:
        res = answer(cfg, [m.model_dump() for m in req.messages], k=req.k, filters=req.filters, mode=req.mode)
    except GenerationError as exc:
        raise HTTPException(502, str(exc)) from exc
    except (ValueError, ConfigError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"pipeline": pipeline, "answer": res.answer, "query": res.query, "citations": res.citations,
            "hits": [_hit(h) for h in res.hits]}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    checks: dict[str, str] = {}

    def check(name, fn):
        try:
            fn()
            checks[name] = "ok"
        except Exception as exc:
            checks[name] = f"{type(exc).__name__}: {exc}"

    def broker():
        from service.worker import celery_app

        if celery_app.conf.task_always_eager:
            return
        with celery_app.connection_for_write() as conn:
            conn.ensure_connection(max_retries=1)

    check("db", JobStore().ping)
    check("broker", broker)
    check("storage", lambda: _storage().list("uploads/"))
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "unavailable", "checks": checks}, status_code=200 if ok else 503)
