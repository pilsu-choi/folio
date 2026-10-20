"""Celery worker. Late acks + reject-on-lost: a job interrupted by a restart is redelivered.

Re-running a job is safe: doc_id/chunk_ids are deterministic and the sink replaces by doc_id.
Worker liveness: `celery -A service.worker:celery_app inspect ping -d celery@$HOSTNAME`.
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.signals import setup_logging

from folio import logging as flog
from folio.logging import log_event
from service import settings
from service.jobs import JobStore

celery_app = Celery("folio", broker=settings.broker_url())
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_serializer="json",
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
)


@setup_logging.connect
def _setup_logging(**_):
    """Connecting here stops Celery from configuring root logging; our JSON handlers also carry its own records."""
    flog.configure()
    celery_log = logging.getLogger("celery")
    celery_log.handlers = list(logging.getLogger("service").handlers)
    celery_log.setLevel(logging.getLogger("service").level)
    celery_log.propagate = False


def source_key(pipeline: str, filename: str) -> str:
    """Stable logical source id: same pipeline + filename + content -> same doc_id on re-upload."""
    return f"upload://{pipeline}/{filename}"


@celery_app.task(name="folio.ingest")
def ingest(job_id: str) -> dict:
    store = JobStore()
    job = store.get(job_id)
    if job is None:
        raise ValueError(f"unknown job {job_id}")
    token = flog.job_id.set(job_id)
    try:
        return _run(store, job, job_id)
    finally:
        flog.job_id.reset(token)


def _run(store: JobStore, job: dict, job_id: str) -> dict:
    from folio.pipeline import load_pipeline, run_pipeline

    store.update(job_id, status="running", error=None, stages={})
    log_event("job_start", logger="service", pipeline=job["pipeline"])

    def progress(stage: str, status: str, ms: int) -> None:
        store.set_stage(job_id, stage, status, ms)
        if status != "running":
            log_event("job_stage", logger="service", stage=stage, status=status, ms=ms)

    try:
        cfg = load_pipeline(job["pipeline"], settings.config_dir())
        res = run_pipeline(
            job["source_uri"], cfg,
            source_uri=source_key(job["pipeline"], job["filename"] or job_id), progress=progress,
        )
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        store.update(job_id, status="failed", error=msg)
        log_event("job_failed", logging.ERROR, "service", exc_info=True, error=msg, error_class=type(exc).__name__)
        return {"job_id": job_id, "status": "failed"}
    err = "; ".join(str(e) for e in res.errors) or None
    store.update(job_id, status=res.status, doc_id=res.doc_id, error=err)
    log_event("job_done", logger="service", status=res.status, doc_id=res.doc_id)
    return {"job_id": job_id, "status": res.status}
