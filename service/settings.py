"""Environment-driven settings (read lazily so tests can monkeypatch)."""

from __future__ import annotations

import os


def config_dir() -> str:
    return os.environ.get("FOLIO_CONFIG_DIR") or os.getcwd()


def database_url() -> str:
    return os.environ.get("FOLIO_DATABASE_URL") or "sqlite:///.folio/jobs.db"


def broker_url() -> str:
    return os.environ.get("FOLIO_BROKER_URL") or "memory://"


def default_pipeline() -> str:
    return os.environ.get("FOLIO_DEFAULT_PIPELINE") or "contracts"


def upload_storage_uri() -> str:
    """Uploads live in the same storage as artifacts (FOLIO_STORAGE_URI, else the pipeline's)."""
    return os.environ.get("FOLIO_STORAGE_URI") or ".folio/artifacts"
