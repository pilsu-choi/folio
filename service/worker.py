"""Celery worker. Late acks + reject-on-lost: a job interrupted by a restart is redelivered.

Re-running a job is safe: doc_id/chunk_ids are deterministic and the sink replaces by doc_id.
Worker liveness: `celery -A service.worker:celery_app inspect ping -d celery@$HOSTNAME`.
"""

from __future__ import annotations

from celery import Celery

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
)


def source_key(pipeline: str, filename: str) -> str:
    """Stable logical source id: same pipeline + filename + content -> same doc_id on re-upload."""
    return f"upload://{pipeline}/{filename}"


@celery_app.task(name="folio.ingest")
def ingest(job_id: str) -> dict:
    from folio.pipeline import load_pipeline, run_pipeline

    store = JobStore()
    job = store.get(job_id)
    if job is None:
        raise ValueError(f"unknown job {job_id}")
    store.update(job_id, status="running", error=None, stages={})
    log_event("job_start", job_id=job_id, pipeline=job["pipeline"])

    def progress(stage: str, status: str, ms: int) -> None:
        store.set_stage(job_id, stage, status, ms)
        if status != "running":
            log_event("job_stage", job_id=job_id, stage=stage, status=status, ms=ms)

    try:
        cfg = load_pipeline(job["pipeline"], settings.config_dir())
        res = run_pipeline(
            job["source_uri"], cfg,
            source_uri=source_key(job["pipeline"], job["filename"] or job_id), progress=progress,
        )
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        store.update(job_id, status="failed", error=msg)
        log_event("job_failed", job_id=job_id, error=msg)
        return {"job_id": job_id, "status": "failed"}
    err = "; ".join(str(e) for e in res.errors) or None
    store.update(job_id, status=res.status, doc_id=res.doc_id, error=err)
    log_event("job_done", job_id=job_id, status=res.status, doc_id=res.doc_id)
    return {"job_id": job_id, "status": res.status}
