"""Environment-driven settings for the chat app (read lazily so tests can monkeypatch)."""

from __future__ import annotations

import json
import os


def auth_mode() -> str:
    mode = os.environ.get("FOLIO_APP_AUTH") or "oidc"  # fail closed: dev (no login) must be asked for
    if mode not in ("dev", "oidc"):
        raise ValueError(f"FOLIO_APP_AUTH must be dev or oidc, got {mode!r}")
    return mode


def secret_key() -> str | None:
    return os.environ.get("FOLIO_APP_SECRET_KEY")


def cookie_secure() -> bool:
    default = "true" if auth_mode() == "oidc" else "false"
    return (os.environ.get("FOLIO_APP_COOKIE_SECURE") or default).lower() in ("1", "true", "yes")


def database_url() -> str:
    return os.environ.get("FOLIO_APP_DATABASE_URL") or "sqlite:///.folio/app.db"


def folio_api_url() -> str:
    return (os.environ.get("FOLIO_API_URL") or "http://localhost:8080").rstrip("/")


def pipelines() -> list[str]:
    """Pipelines users may pick; empty = always the folio server's default pipeline."""
    return [p.strip() for p in (os.environ.get("FOLIO_APP_PIPELINES") or "").split(",") if p.strip()]


def acl_claims() -> dict[str, str]:
    """``{filter_field: claim}``: search filters fixed from the login token, e.g. ``{"tenant_id": "tenant"}``."""
    return json.loads(os.environ.get("FOLIO_APP_ACL_CLAIMS") or "{}")


def oidc() -> dict:
    issuer = (os.environ.get("OIDC_ISSUER") or "").rstrip("/")
    return {
        "server_metadata_url": os.environ.get("OIDC_METADATA_URL") or f"{issuer}/.well-known/openid-configuration",
        "client_id": os.environ.get("OIDC_CLIENT_ID"),
        "client_secret": os.environ.get("OIDC_CLIENT_SECRET"),
        "scope": os.environ.get("OIDC_SCOPES") or "openid email profile",
    }


def public_url() -> str | None:
    """External base URL (behind a proxy) used for the OIDC redirect URI; default: derived from the request."""
    return (os.environ.get("FOLIO_APP_PUBLIC_URL") or "").rstrip("/") or None


def dev_user() -> dict:
    """The fixed user in dev mode; ``FOLIO_APP_DEV_CLAIMS`` (JSON) feeds the ACL claims."""
    return {"sub": os.environ.get("FOLIO_APP_DEV_USER") or "dev", "email": "dev@localhost", "name": "개발자",
            **json.loads(os.environ.get("FOLIO_APP_DEV_CLAIMS") or "{}")}
