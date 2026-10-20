"""Structured JSON-lines logging: `log_event("stage", job_id=..., stage=..., ms=...)`."""

from __future__ import annotations

import json
import logging
import os
import sys
import time

_LOGGER = "folio"


def configure() -> None:
    logger = logging.getLogger(_LOGGER)
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(os.environ.get("FOLIO_LOG_LEVEL", "INFO").upper())
    logger.propagate = False


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    configure()
    rec = {"ts": round(time.time(), 3), "event": event, **fields}
    logging.getLogger(_LOGGER).log(level, json.dumps(rec, ensure_ascii=False, default=str))
