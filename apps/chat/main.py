"""folio chat app: login (SSO), stored conversations, and answers from folio ``POST /v1/chat``.

Run: ``uvicorn --factory apps.chat.main:create_app``. folio itself has no auth and stays on the internal network; this app is the
user-facing edge. It rebuilds the history from its database, fixes the ACL filters from the login token, calls
folio server to server, and stores the turn only when the answer succeeded.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from apps.chat import auth, settings
from apps.chat.auth import current_user
from apps.chat.store import ChatStore
from folio.logging import clip, configure, log_event, ms_since, redact_url, request_id

MAX_MESSAGES = 50  # folio /v1/chat limits
MAX_CONTENT = 20000
PAGE_ZOOM = 2  # page preview: 2 px per pt (an A4 page is 1190 px wide)
PAGE_MARGIN = 36  # pt of context around the cited area
STATIC = os.path.join(os.path.dirname(__file__), "static")
router = APIRouter()
RID = "X-Request-ID"
QUIET = ("/static", "/healthz")  # logged at DEBUG
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

    configure()
    secret = settings.secret_key()
    if not secret:
        if settings.auth_mode() == "oidc":
            raise RuntimeError("FOLIO_APP_AUTH=oidc needs FOLIO_APP_SECRET_KEY (session cookie signing key)")
        secret = secrets.token_urlsafe(32)
    app = FastAPI(title="folio chat", lifespan=lifespan)

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        rid = (request.headers.get(RID) or "").strip()[:64] or uuid.uuid4().hex[:16]
        token, t0 = request_id.set(rid), time.perf_counter()
        status, level = 500, logging.INFO
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:  # noqa: BLE001 - log and answer 500 like the default handler
            log_event("unhandled_exception", logging.ERROR, "apps.chat", exc_info=True, method=request.method,
                      path=request.url.path)
            response = JSONResponse({"detail": "internal server error"}, status_code=500)
        if request.url.path == "/" or request.url.path.startswith(QUIET):
            level = logging.DEBUG
        log_event("http_request", level, "apps.chat", method=request.method, path=request.url.path, status=status,
                  ms=ms_since(t0), user=getattr(request.state, "user_id", None))
        response.headers[RID] = rid
        request_id.reset(token)
        return response

    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="folio_chat", same_site="lax",
                       https_only=settings.cookie_secure(), max_age=12 * 3600)
    app.state.store = ChatStore(settings.database_url())
    log_event("app_start", logging.INFO, "apps.chat", auth=settings.auth_mode(), database=redact_url(settings.database_url()),
              folio_api=redact_url(settings.folio_api_url()))
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
    log_event("chat_request", logging.DEBUG, "apps.chat", conversation=cid, messages=len(history),
              question=clip(body.content), payload_chars=sum(len(m["content"]) for m in history))
    answer = _ask_folio(request.app.state.folio, req)
    answer["hits"] = [{**h, "record": {k: v for k, v in (h.get("record") or {}).items() if k != "embedding"}}
                      for h in answer.get("hits") or []]  # vectors are useless to the UI and bloat every row
    turn = store.add_turn(user["sub"], cid, body.content, answer)
    return {"messages": [_msg(m) for m in turn], "conversation": _conv(store.get_conversation(user["sub"], cid))}


def _call_folio(client: httpx.Client, method: str, path: str, **kw) -> httpx.Response:
    """One folio API call: forwards the request id, logs status and ms (4xx WARNING, 5xx/transport ERROR)."""
    headers = {RID: rid} if (rid := request_id.get()) else {}
    t0 = time.perf_counter()
    try:
        r = client.request(method, path, headers=headers, **kw)
    except httpx.HTTPError as exc:
        log_event("folio_call", logging.ERROR, "apps.chat", exc_info=True, endpoint=path, error=type(exc).__name__,
                  ms=ms_since(t0))
        raise HTTPException(503, f"search service unavailable: {type(exc).__name__}") from exc
    level = logging.ERROR if r.status_code >= 500 else logging.WARNING if r.status_code >= 400 else logging.INFO
    extra = {"body": clip(r.text, 300)} if r.status_code >= 400 else {}
    log_event("folio_call", level, "apps.chat", endpoint=path, status=r.status_code, ms=ms_since(t0), **extra)
    return r


def _ask_folio(client: httpx.Client, req: dict) -> dict:
    r = _call_folio(client, "POST", "/v1/chat", json=req)
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


def _hit_record(request: Request, user: dict, cid: str, mid: int, n: int) -> tuple[dict, dict]:
    """Source n of a stored answer: users only open documents that their own (ACL-filtered) answers returned."""
    conv = _owned(request, user, cid)
    msg = next((m for m in _store(request).list_messages(user["sub"], cid) if m["id"] == mid), None)
    hits = (msg or {}).get("hits") or []
    if msg is None or msg["role"] != "assistant" or not 1 <= n <= len(hits):
        raise HTTPException(404, "source not found")
    return conv, {**(hits[n - 1].get("record") or {}), "doc_id": hits[n - 1].get("doc_id")}


def _source_pdf(request: Request, conv: dict, record: dict) -> bytes:
    params = {"pipeline": conv["pipeline"]} if conv["pipeline"] else {}
    r = _call_folio(request.app.state.folio, "GET", f"/v1/documents/{record['doc_id']}/pdf", params=params)
    if r.status_code in (400, 404):
        raise HTTPException(404, "the original PDF is not available for this source")
    if r.status_code != 200:
        raise HTTPException(503, f"search service error {r.status_code}")
    return r.content


@router.get("/api/conversations/{cid}/messages/{mid}/sources/{n}/pdf")
def source_pdf(cid: str, mid: int, n: int, request: Request, user: User):
    conv, record = _hit_record(request, user, cid, mid, n)
    return Response(_source_pdf(request, conv, record), media_type="application/pdf",
                    headers={"Cache-Control": "private, max-age=600"})


@router.get("/api/conversations/{cid}/messages/{mid}/sources/{n}/page.png")
def source_page(cid: str, mid: int, n: int, request: Request, user: User):
    """The cited area of the page, highlighted: the chunk's bbox (pt, top-left origin of the visible page) plus context."""
    import pymupdf

    conv, record = _hit_record(request, user, cid, mid, n)
    if not record.get("page"):
        raise HTTPException(404, "this source has no page")
    doc = pymupdf.open(stream=_source_pdf(request, conv, record), filetype="pdf")
    if not 1 <= record["page"] <= doc.page_count:
        raise HTTPException(404, "page not in the PDF")
    page = doc[record["page"] - 1]
    clip = None
    if record.get("bbox"):
        box = pymupdf.Rect(record["bbox"])
        page.draw_rect((box + (-2, -2, 2, 2)) * page.derotation_matrix, color=(0.95, 0.55, 0), fill=(1, 0.85, 0.2),
                       fill_opacity=0.3, width=1.5)
        # the source panel is narrow: show the cited area with some context, not the whole page
        clip = (box + (-PAGE_MARGIN, -PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)) & page.rect  # visible-page coords
    png = page.get_pixmap(matrix=pymupdf.Matrix(PAGE_ZOOM, PAGE_ZOOM), clip=clip).tobytes("png")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=600"})


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

