"""Login: OIDC authorization code flow (PKCE, ID token checked by Authlib) or a fixed dev user.

The session cookie (signed, HttpOnly, SameSite=Lax) holds ``{sub, email, name, acl}``. ``acl`` is the search
filters fixed at login from token claims (``FOLIO_APP_ACL_CLAIMS``); the client can never override them.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from apps.chat import settings
from folio.logging import log_event, redact_url

router = APIRouter()
_oauth = None


def _log(event: str, level: int = logging.INFO, **fields) -> None:
    log_event(event, level, "apps.chat.auth", **fields)


def _idp():
    global _oauth
    if _oauth is None:
        from authlib.integrations.starlette_client import OAuth

        cfg = settings.oidc()
        if not cfg["client_id"]:
            raise RuntimeError("FOLIO_APP_AUTH=oidc needs OIDC_ISSUER, OIDC_CLIENT_ID and OIDC_CLIENT_SECRET")
        _oauth = OAuth()
        _oauth.register("idp", server_metadata_url=cfg["server_metadata_url"], client_id=cfg["client_id"],
                        client_secret=cfg["client_secret"],
                        client_kwargs={"scope": cfg["scope"], "code_challenge_method": "S256"})
    return _oauth.idp


def acl_from(claims: dict) -> dict | None:
    """Filters from claims, or ``None`` when a mapped claim is missing (folio drops ``None`` filters, which would
    silently widen the search, so such a user may not search at all)."""
    acl = {}
    for field, claim in settings.acl_claims().items():
        value = claims.get(claim)
        if value in (None, "", []):
            return None
        acl[field] = value
    return acl


def session_user(claims: dict) -> dict:
    return {"sub": str(claims["sub"]), "email": claims.get("email"),
            "name": claims.get("name") or claims.get("preferred_username") or claims.get("email"),
            "acl": acl_from(claims)}


def current_user(request: Request) -> dict:
    if settings.auth_mode() == "dev":
        user = session_user(settings.dev_user())
    else:
        user = request.session.get("user")
        if not user:
            raise HTTPException(401, "login required")
    request.state.user_id = user["sub"]
    return user


def _callback_url(request: Request) -> str:
    base = settings.public_url()
    return f"{base}/auth/callback" if base else str(request.url_for("auth_callback"))


@router.get("/auth/login")
async def login(request: Request):
    if settings.auth_mode() == "dev":
        return RedirectResponse("../")
    _log("login_start", issuer=redact_url(settings.oidc()["server_metadata_url"]).split("/.well-known")[0])
    return await _idp().authorize_redirect(request, _callback_url(request))


@router.get("/auth/callback", name="auth_callback")
async def callback(request: Request):
    from authlib.integrations.base_client import OAuthError

    try:
        token = await _idp().authorize_access_token(request)
    except OAuthError as exc:
        _log("login_failed", logging.WARNING, error=exc.error)
        raise HTTPException(401, f"login failed: {exc.error}") from exc
    claims = dict(token.get("userinfo") or await _idp().userinfo(token=token))
    user = session_user(claims)
    request.app.state.store.upsert_user(user["sub"], user["email"], user["name"])
    request.session.clear()
    request.session["user"] = user
    _log("login_success", user=user["sub"], acl_keys=sorted(user["acl"] or []))
    return RedirectResponse("../")


@router.post("/auth/logout", status_code=204)
def logout(request: Request):
    _log("logout", user=(request.session.get("user") or {}).get("sub"))
    request.session.clear()
    return Response(status_code=204)
