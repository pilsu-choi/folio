"""`jobs` table (SQLAlchemy Core). States: queued -> running -> succeeded | partial | failed."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from folio.logging import log_event, redact_url
from service import settings

metadata = sa.MetaData()
jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("pipeline", sa.String(128), nullable=False),
    sa.Column("source_uri", sa.Text, nullable=False),
    sa.Column("filename", sa.Text),
    sa.Column("status", sa.String(16), nullable=False, index=True),
    sa.Column("stages", sa.JSON, nullable=False),
    sa.Column("doc_id", sa.String(64)),
    sa.Column("error", sa.Text),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

_ENGINES: dict[str, sa.Engine] = {}


def _now() -> datetime:
    return datetime.now(UTC)


class JobStore:
    def __init__(self, url: str | None = None):
        self.url = url or settings.database_url()
        if self.url not in _ENGINES:
            if self.url.startswith("sqlite:///") and ":memory:" not in self.url:
                import pathlib

                pathlib.Path(self.url[len("sqlite:///") :]).parent.mkdir(parents=True, exist_ok=True)
            _ENGINES[self.url] = sa.create_engine(self.url, pool_pre_ping=True)
            log_event("db_engine", logging.DEBUG, "service", url=redact_url(self.url))
        self.engine = _ENGINES[self.url]

    def create_all(self) -> None:
        metadata.create_all(self.engine, checkfirst=True)

    def ping(self) -> bool:
        with self.engine.connect() as c:
            c.execute(sa.text("select 1"))
        return True

    def create(self, pipeline: str, source_uri: str, filename: str, job_id: str | None = None) -> str:
        job_id = job_id or str(uuid.uuid4())
        now = _now()
        with self.engine.begin() as c:
            c.execute(
                jobs.insert().values(
                    id=job_id, pipeline=pipeline, source_uri=source_uri, filename=filename,
                    status="queued", stages={}, created_at=now, updated_at=now,
                )
            )
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            row = c.execute(jobs.select().where(jobs.c.id == job_id)).mappings().first()
        return dict(row) if row else None

    def update(self, job_id: str, **values: Any) -> None:
        with self.engine.begin() as c:
            c.execute(jobs.update().where(jobs.c.id == job_id).values(updated_at=_now(), **values))

    def set_stage(self, job_id: str, stage: str, status: str, ms: int) -> None:
        with self.engine.begin() as c:
            cur = c.execute(sa.select(jobs.c.stages).where(jobs.c.id == job_id)).scalar() or {}
            cur = {**cur, stage: {"status": status, "ms": ms}}
            c.execute(jobs.update().where(jobs.c.id == job_id).values(stages=cur, updated_at=_now()))
