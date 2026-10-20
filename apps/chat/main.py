"""folio chat app: login (SSO), stored conversations, and answers from folio ``POST /v1/chat``.

Run: ``uvicorn --factory apps.chat.main:create_app``. folio itself has no auth and stays on the internal network; this app is the
user-facing edge. It rebuilds the history from its database, fixes the ACL filters from the login token, calls
folio server to server, and stores the turn only when the answer succeeded.
"""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from apps.chat import auth, settings
from apps.chat.auth import current_user
from apps.chat.store import ChatStore

MAX_MESSAGES = 50  # folio /v1/chat limits
MAX_CONTENT = 20000
STATIC = os.path.join(os.path.dirname(__file__), "static")
router = APIRouter()
User = Annotated[dict, Depends(current_user)]


def create_app(transport: httpx.BaseTransport | None = None) -> FastAPI:
    """``transport`` replaces the HTTP transport to folio (tests)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store.create_all()
        if settings.auth_mode() == "dev":
            dev = settings.dev_user()
            app.state.store.upsert_user(dev["sub"], dev["email"], dev["name"])
        yield
        app.state.folio.close()

    secret = settings.secret_key()
    if not secret:
        if settings.auth_mode() == "oidc":
            raise RuntimeError("FOLIO_APP_AUTH=oidc needs FOLIO_APP_SECRET_KEY (session cookie signing key)")
        secret = secrets.token_urlsafe(32)
    app = FastAPI(title="folio chat", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="folio_chat", same_site="lax",
                       https_only=settings.cookie_secure(), max_age=12 * 3600)
    app.state.store = ChatStore(settings.database_url())
    app.state.folio = httpx.Client(base_url=settings.folio_api_url(), timeout=180, transport=transport)
    app.include_router(auth.router)
    app.include_router(router)
    return app


class ConversationIn(BaseModel):
    title: str = Field(default="", max_length=200)
    pipeline: str | None = None


class TitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_CONTENT)
    k: int | None = Field(default=None, ge=1, le=100)
    mode: Literal["hybrid", "bm25", "vector"] | None = None
    filters: dict = Field(default_factory=dict)


class FeedbackIn(BaseModel):
    feedback: Literal["up", "down"] | None


def _store(request: Request) -> ChatStore:
    return request.app.state.store


def _conv(c: dict) -> dict:
    return {**c, "created_at": _iso(c["created_at"]), "updated_at": _iso(c["updated_at"])}


def _msg(m: dict) -> dict:
    return {**m, "created_at": _iso(m["created_at"])}


def _iso(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()  # SQLite drops the zone (UTC)


def _owned(request: Request, user: dict, cid: str) -> dict:
    conv = _store(request).get_conversation(user["sub"], cid)
    if conv is None:
        raise HTTPException(404, "conversation not found")
    return conv


@router.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/api/me")
def me(user: User):
    return {"sub": user["sub"], "email": user["email"], "name": user["name"], "auth_mode": settings.auth_mode(),
            "pipelines": settings.pipelines(), "acl_keys": sorted(settings.acl_claims())}


@router.get("/api/conversations")
def list_conversations(request: Request, user: User):
    return [_conv(c) for c in _store(request).list_conversations(user["sub"])]


@router.post("/api/conversations", status_code=201)
def create_conversation(body: ConversationIn, request: Request, user: User):
    allowed = settings.pipelines()
    if body.pipeline and body.pipeline not in allowed:
        raise HTTPException(400, f"pipeline must be one of {allowed}" if allowed else "pipeline is fixed")
    store = _store(request)
    store.upsert_user(user["sub"], user["email"], user["name"])  # e.g. a session older than the database
    return _conv(store.create_conversation(user["sub"], body.title.strip(), body.pipeline))


@router.get("/api/conversations/{cid}")
def get_conversation(cid: str, request: Request, user: User):
    conv = _owned(request, user, cid)
    return {**_conv(conv), "messages": [_msg(m) for m in _store(request).list_messages(user["sub"], cid)]}


@router.patch("/api/conversations/{cid}")
def rename_conversation(cid: str, body: TitleIn, request: Request, user: User):
    conv = _store(request).rename_conversation(user["sub"], cid, body.title.strip())
    if conv is None:
        raise HTTPException(404, "conversation not found")
    return _conv(conv)


@router.delete("/api/conversations/{cid}", status_code=204)
def delete_conversation(cid: str, request: Request, user: User):
    if not _store(request).delete_conversation(user["sub"], cid):
        raise HTTPException(404, "conversation not found")
    return Response(status_code=204)


@router.get("/api/conversations/{cid}/messages")
def list_messages(cid: str, request: Request, user: User):
    _owned(request, user, cid)
    return [_msg(m) for m in _store(request).list_messages(user["sub"], cid)]


@router.post("/api/conversations/{cid}/messages", status_code=201)
def send_message(cid: str, body: MessageIn, request: Request, user: User):
    conv = _owned(request, user, cid)
    if user["acl"] is None:
        raise HTTPException(403, "your login has no access claims for search; ask an administrator")
    store = _store(request)
    history = [{"role": m["role"], "content": m["content"][:MAX_CONTENT]}
               for m in store.list_messages(user["sub"], cid)]
    history = (history + [{"role": "user", "content": body.content}])[-MAX_MESSAGES:]
    while history[0]["role"] != "user":
        history.pop(0)
    req = {"messages": history, "filters": {**body.filters, **user["acl"]}}  # login ACL wins over the client
    for key, value in (("pipeline", conv["pipeline"]), ("k", body.k), ("mode", body.mode)):
        if value is not None:
            req[key] = value
    answer = _ask_folio(request.app.state.folio, req)
    answer["hits"] = [{**h, "record": {k: v for k, v in (h.get("record") or {}).items() if k != "embedding"}}
                      for h in answer.get("hits") or []]  # vectors are useless to the UI and bloat every row
    turn = store.add_turn(user["sub"], cid, body.content, answer)
    return {"messages": [_msg(m) for m in turn], "conversation": _conv(store.get_conversation(user["sub"], cid))}


def _ask_folio(client: httpx.Client, req: dict) -> dict:
    try:
        r = client.post("/v1/chat", json=req)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"search service unavailable: {type(exc).__name__}") from exc
    if r.status_code == 200:
        return r.json()
    try:
        detail = r.json().get("detail")
    except ValueError:
        detail = r.text[:500]
    if r.status_code in (400, 422):
        raise HTTPException(400, detail)
    if r.status_code == 502:
        raise HTTPException(502, detail or "answer generation failed")
    raise HTTPException(503, f"search service error {r.status_code}")


@router.patch("/api/messages/{mid}")
def set_feedback(mid: int, body: FeedbackIn, request: Request, user: User):
    msg = _store(request).set_feedback(user["sub"], mid, body.feedback)
    if msg is None:
        raise HTTPException(404, "message not found")
    return _msg(msg)


@router.delete("/api/conversations/{cid}/messages/{mid}", status_code=204)
def delete_from(cid: str, mid: int, request: Request, user: User):
    if not _store(request).truncate(user["sub"], cid, mid):
        raise HTTPException(404, "message not found")
    return Response(status_code=204)

