"""Structured JSON-lines logging for the library, the API/worker (``service``) and the chat app (``apps``).

Every line is one JSON object: ``{"ts", "level", "logger", "event", ...fields}`` plus ``request_id``/``job_id`` when
set in the current context. ``log_event("stage", stage=..., ms=...)`` and plain
``logging.getLogger(__name__).info("msg")`` produce the same shape.

Level: ``FOLIO_LOG_LEVEL`` (default INFO; DEBUG adds request/response details and truncated user content, so keep
it off in production). Output: stderr (containers: the runtime stores and rotates it); ``FOLIO_LOG_FILE`` also writes
a rotating file (``FOLIO_LOG_FILE_MAX_MB`` default 50, ``FOLIO_LOG_FILE_BACKUPS`` default 5) for runs outside
containers. Only the ``folio``, ``service`` and ``apps`` loggers are configured, never the root logger, so a host
application (Airflow, a notebook) keeps its own logging.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import os
import re
import sys
import time
from collections.abc import Callable
from typing import Any

NAMESPACES = ("folio", "service", "apps")
request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("job_id", default=None)
_CONFIGURED: dict[str, Any] = {}
_STD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName", "color_message"}
_CRED = re.compile(r"(?<=://)[^/@\s]+:[^/@\s]*@")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rec = {"ts": round(record.created, 3), "level": record.levelname, "logger": record.name}
        if (rid := request_id.get()) is not None:
            rec["request_id"] = rid
        if (jid := job_id.get()) is not None:
            rec["job_id"] = jid
        fields = getattr(record, "fields", None)
        if fields is None:  # plain logger call: message + any `extra=` keys
            rec["event"] = record.getMessage()
            fields = {k: v for k, v in vars(record).items() if k not in _STD}
        else:
            rec["event"] = record.msg
        rec.update(fields)
        if record.exc_info:
            rec["exc"] = self.formatException(record.exc_info)
        return json.dumps(rec, ensure_ascii=False, default=str)


def _level(value: str | int | None) -> int:
    if isinstance(value, int):
        return value
    name = (value or os.environ.get("FOLIO_LOG_LEVEL") or "INFO").upper()
    level = logging.getLevelName(name)
    if not isinstance(level, int):
        print(json.dumps({"level": "WARNING", "event": f"invalid log level {name!r}, using INFO"}), file=sys.stderr)
        return logging.INFO
    return level


def configure(level: str | int | None = None, force: bool = False) -> int:
    """Attach the JSON handler(s) to the folio/service/apps loggers. Idempotent; ``level`` or ``force`` re-applies
    (e.g. after ``FOLIO_LOG_FILE`` changed). Returns the effective level."""
    if _CONFIGURED and level is None and not force:
        return _CONFIGURED["level"]
    lvl = _level(level)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if path := os.environ.get("FOLIO_LOG_FILE"):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            path, maxBytes=int(os.environ.get("FOLIO_LOG_FILE_MAX_MB", "50")) * 2**20,
            backupCount=int(os.environ.get("FOLIO_LOG_FILE_BACKUPS", "5")), encoding="utf-8"))
    fmt = JsonFormatter()
    for h in handlers:
        h.setFormatter(fmt)
    for old in _CONFIGURED.get("handlers", []):
        for name in NAMESPACES:
            logging.getLogger(name).removeHandler(old)
        old.close()
    for name in NAMESPACES:
        logger = logging.getLogger(name)
        for h in handlers:
            logger.addHandler(h)
        logger.setLevel(lvl)
        logger.propagate = False
    _CONFIGURED.update(level=lvl, handlers=handlers)
    return lvl


def adopt(*names: str) -> None:
    """Route third-party loggers (``uvicorn``, ``celery``) through the same JSON handlers and level."""
    lvl = configure()
    for name in names:
        log = logging.getLogger(name)
        log.handlers = list(_CONFIGURED["handlers"])
        log.setLevel(lvl)
        log.propagate = False


def log_event(event: str, level: int = logging.INFO, logger: str = "folio", exc_info=None, **fields) -> None:
    """One structured line: ``event`` plus ``fields``."""
    configure()
    log = logging.getLogger(logger)
    if log.isEnabledFor(level):
        log.log(level, event, extra={"fields": fields}, exc_info=exc_info)


def debug_enabled(logger: str = "folio") -> bool:
    configure()
    return logging.getLogger(logger).isEnabledFor(logging.DEBUG)


def clip(text: Any, n: int = 500) -> str:
    """Truncate user content / LLM replies for DEBUG lines."""
    s = str(text)
    return s if len(s) <= n else f"{s[:n]}…(+{len(s) - n} chars)"


def redact_url(url: str) -> str:
    """Strip ``user:password@`` from URLs/DSNs before logging."""
    return _CRED.sub("***@", url or "")


def ms_since(t0: float) -> int:
    """Milliseconds since a ``time.perf_counter()`` value."""
    return round((time.perf_counter() - t0) * 1000)


def with_context(fn: Callable) -> Callable:
    """Run ``fn`` in a copy of the caller's context, so request_id/job_id survive a ThreadPoolExecutor."""
    ctx = contextvars.copy_context()
    return lambda *a, **k: ctx.copy().run(fn, *a, **k)
