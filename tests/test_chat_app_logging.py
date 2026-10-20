import json
import logging

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("itsdangerous")
pytest.importorskip("authlib")
import httpx
from fastapi.testclient import TestClient

from apps.chat.main import create_app
from folio.logging import JsonFormatter, configure

SECRET = "s3cr3t-app-key-xyz"
TOKEN = "tok-abc123-do-not-log"


class Lines(logging.Handler):
    """Formats at emit time, while request_id is still set."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.raw: list[str] = []

    def emit(self, record):
        self.raw.append(self.format(record))


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setenv("FOLIO_APP_DATABASE_URL", f"sqlite:///{tmp_path}/app.db")
    for k in ("FOLIO_APP_ACL_CLAIMS", "FOLIO_APP_PIPELINES", "FOLIO_APP_DEV_CLAIMS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FOLIO_APP_AUTH", "dev")
    monkeypatch.setenv("FOLIO_APP_SECRET_KEY", SECRET)
    configure("DEBUG")
    handler, apps = Lines(), logging.getLogger("apps")
    apps.addHandler(handler)
    yield handler
    apps.removeHandler(handler)
    configure("INFO")


class Folio:
    def __init__(self, status=200):
        self.headers, self.status = [], status

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.headers.append(req.headers.get("x-request-id"))
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "boom"})
        return httpx.Response(200, json={"pipeline": "p", "answer": "a", "query": "q", "citations": [], "hits": []})


def lines(handler) -> list[dict]:
    return [json.loads(x) for x in handler.raw]


def ask(folio, **headers):
    with TestClient(create_app(transport=httpx.MockTransport(folio))) as c:
        cid = c.post("/api/conversations", json={}).json()["id"]
        return c.post(f"/api/conversations/{cid}/messages", json={"content": "hello"}, headers=headers)


def test_request_id_forwarded_and_http_line(logs):
    folio = Folio()
    r = ask(folio, **{"X-Request-ID": "rid-42"})
    assert r.status_code == 201 and r.headers["x-request-id"] == "rid-42"
    assert folio.headers == ["rid-42"]
    rows = lines(logs)
    http = [x for x in rows if x["event"] == "http_request" and x["path"].endswith("/messages")]
    assert http and http[0]["level"] == "INFO" and http[0]["status"] == 201 and http[0]["request_id"] == "rid-42"
    assert http[0]["user"] and "@" not in str(http[0]["user"])
    call = next(x for x in rows if x["event"] == "folio_call")
    assert call["status"] == 200 and call["endpoint"] == "/v1/chat" and call["request_id"] == "rid-42"


def test_request_id_generated(logs):
    r = ask(Folio())
    assert r.headers["x-request-id"]


def test_folio_500_logs_error(logs):
    assert ask(Folio(500)).status_code == 503
    assert any(x["event"] == "folio_call" and x["level"] == "ERROR" and x["status"] == 500 for x in lines(logs))


def test_folio_400_logs_warning(logs):
    ask(Folio(422))
    assert any(x["event"] == "folio_call" and x["level"] == "WARNING" for x in lines(logs))


def test_unhandled_exception_logged(logs):
    app = create_app(transport=httpx.MockTransport(Folio()))

    @app.get("/boom")
    def boom():
        raise ValueError("kaput")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom")
    assert r.status_code == 500 and r.headers["x-request-id"]
    err = [x for x in lines(logs) if x["event"] == "unhandled_exception"]
    assert err and err[0]["level"] == "ERROR" and "ValueError" in err[0]["exc"]


def test_secrets_never_logged(logs):
    ask(Folio(500), Authorization=f"Bearer {TOKEN}", Cookie=f"folio_chat={TOKEN}")
    text = "".join(logs.raw)
    assert SECRET not in text and TOKEN not in text
