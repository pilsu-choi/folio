import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("itsdangerous")
pytest.importorskip("authlib")
import httpx
from fastapi.testclient import TestClient

from apps.chat import auth
from apps.chat.main import create_app


class Folio:
    """Fake folio /v1/chat: records requests, answers with one cited hit (or a scripted failure)."""

    def __init__(self):
        self.requests, self.fail = [], None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        self.requests.append(body)
        if self.fail == "connect":
            raise httpx.ConnectError("down")
        if self.fail:
            return httpx.Response(self.fail, json={"detail": f"failed {self.fail}"})
        q = body["messages"][-1]["content"]
        return httpx.Response(200, json={
            "pipeline": body.get("pipeline", "contracts"), "answer": f"answer to {q} [1]", "query": q,
            "citations": [{"n": 1, "chunk_id": "c1", "doc_id": "d1"}],
            "hits": [{"chunk_id": "c1", "doc_id": "d1", "score": 1.0, "content": "x", "record": {"page": 1}}]})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FOLIO_APP_DATABASE_URL", f"sqlite:///{tmp_path}/app.db")
    for k in ("FOLIO_APP_AUTH", "FOLIO_APP_ACL_CLAIMS", "FOLIO_APP_PIPELINES", "FOLIO_APP_DEV_CLAIMS",
              "FOLIO_APP_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def client(folio: Folio) -> TestClient:
    return TestClient(create_app(transport=httpx.MockTransport(folio)))


def test_conversation_crud_and_multiturn(env):
    folio = Folio()
    with client(folio) as c:
        assert c.get("/api/me").json()["auth_mode"] == "dev"
        conv = c.post("/api/conversations", json={}).json()
        r = c.post(f"/api/conversations/{conv['id']}/messages", json={"content": "  계약 기간은? ", "k": 3})
        assert r.status_code == 201
        user_msg, bot = r.json()["messages"]
        assert user_msg["role"] == "user" and bot["content"] == "answer to   계약 기간은?  [1]"
        assert bot["citations"][0]["n"] == 1 and bot["hits"][0]["record"] == {"page": 1}
        assert r.json()["conversation"]["title"] == "계약 기간은?"
        assert folio.requests[0] == {"messages": [{"role": "user", "content": "  계약 기간은? "}], "filters": {},
                                     "k": 3}

        c.post(f"/api/conversations/{conv['id']}/messages", json={"content": "해지는?", "mode": "bm25"})
        assert [m["role"] for m in folio.requests[1]["messages"]] == ["user", "assistant", "user"]
        assert folio.requests[1]["mode"] == "bm25"

        assert c.patch(f"/api/conversations/{conv['id']}", json={"title": "계약"}).json()["title"] == "계약"
        assert c.patch(f"/api/messages/{bot['id']}", json={"feedback": "up"}).json()["feedback"] == "up"
        assert c.patch(f"/api/messages/{user_msg['id']}", json={"feedback": "up"}).status_code == 404  # user turn
        got = c.get(f"/api/conversations/{conv['id']}").json()
        assert len(got["messages"]) == 4 and got["messages"][1]["feedback"] == "up"
        listed = c.get("/api/conversations").json()
        assert [x["id"] for x in listed] == [conv["id"]] and listed[0]["updated_at"].endswith("+00:00")

        third = got["messages"][2]["id"]  # "delete from here" drops that turn and everything after
        assert c.delete(f"/api/conversations/{conv['id']}/messages/{third}").status_code == 204
        assert len(c.get(f"/api/conversations/{conv['id']}/messages").json()) == 2

        assert c.delete(f"/api/conversations/{conv['id']}").status_code == 204
        assert c.get(f"/api/conversations/{conv['id']}").status_code == 404
        assert c.get("/api/conversations").json() == []


def test_failed_answer_stores_nothing(env):
    folio = Folio()
    with client(folio) as c:
        cid = c.post("/api/conversations", json={}).json()["id"]
        for fail, status in ((502, 502), (400, 400), (500, 503), ("connect", 503)):
            folio.fail = fail
            assert c.post(f"/api/conversations/{cid}/messages", json={"content": "q"}).status_code == status
        assert c.get(f"/api/conversations/{cid}/messages").json() == []
        assert c.get(f"/api/conversations/{cid}").json()["title"] == ""


def test_history_capped_at_folio_limit(env):
    folio = Folio()
    with client(folio) as c:
        cid = c.post("/api/conversations", json={}).json()["id"]
        for i in range(26):  # 52 stored messages
            c.post(f"/api/conversations/{cid}/messages", json={"content": f"q{i}"})
        sent = folio.requests[-1]["messages"]
        assert len(sent) <= 50 and sent[0]["role"] == "user" and sent[-1]["content"] == "q25"


def test_pipeline_whitelist(env):
    env.setenv("FOLIO_APP_PIPELINES", "contracts, manuals")
    folio = Folio()
    with client(folio) as c:
        assert c.get("/api/me").json()["pipelines"] == ["contracts", "manuals"]
        assert c.post("/api/conversations", json={"pipeline": "secret"}).status_code == 400
        cid = c.post("/api/conversations", json={"pipeline": "manuals"}).json()["id"]
        c.post(f"/api/conversations/{cid}/messages", json={"content": "q"})
        assert folio.requests[0]["pipeline"] == "manuals"


class FakeIdP:
    """Stands in for Authlib's registered client: the callback 'exchanges' the code for these claims."""

    def __init__(self):
        self.claims = {}

    async def authorize_redirect(self, request, redirect_uri):
        from fastapi.responses import RedirectResponse

        return RedirectResponse(f"https://idp.test/authorize?redirect_uri={redirect_uri}")

    async def authorize_access_token(self, request):
        return {"access_token": "t", "userinfo": dict(self.claims)}


@pytest.fixture
def oidc(env):
    env.setenv("FOLIO_APP_AUTH", "oidc")
    env.setenv("FOLIO_APP_SECRET_KEY", "s" * 32)
    env.setenv("FOLIO_APP_COOKIE_SECURE", "false")  # TestClient talks http
    env.setenv("FOLIO_APP_ACL_CLAIMS", json.dumps({"tenant_id": "tenant"}))
    idp = FakeIdP()
    env.setattr(auth, "_idp", lambda: idp)
    return idp


def login(c: TestClient, idp: FakeIdP, **claims):
    idp.claims = claims
    r = c.get("/auth/callback?code=x&state=y", follow_redirects=False)
    assert r.status_code in (302, 307)


def test_oidc_login_isolation_and_acl(oidc):
    folio = Folio()
    with client(folio) as a, client(folio) as b:
        assert a.get("/api/me").status_code == 401
        assert a.get("/auth/login", follow_redirects=False).headers["location"].startswith("https://idp.test/")
        login(a, oidc, sub="alice", email="a@x", name="Alice", tenant="t-a")
        login(b, oidc, sub="bob", email="b@x", tenant="t-b")
        assert a.get("/api/me").json() == {"sub": "alice", "email": "a@x", "name": "Alice", "auth_mode": "oidc",
                                           "pipelines": [], "acl_keys": ["tenant_id"]}

        cid = a.post("/api/conversations", json={}).json()["id"]
        r = a.post(f"/api/conversations/{cid}/messages", json={"content": "q", "filters": {
            "tenant_id": "t-b", "doc_type": "contract"}})
        assert r.status_code == 201
        assert folio.requests[-1]["filters"] == {"tenant_id": "t-a", "doc_type": "contract"}  # login ACL wins

        mid = r.json()["messages"][1]["id"]
        assert b.get("/api/conversations").json() == []
        assert b.get(f"/api/conversations/{cid}").status_code == 404
        assert b.post(f"/api/conversations/{cid}/messages", json={"content": "q"}).status_code == 404
        assert b.patch(f"/api/conversations/{cid}", json={"title": "x"}).status_code == 404
        assert b.patch(f"/api/messages/{mid}", json={"feedback": "down"}).status_code == 404
        assert b.delete(f"/api/conversations/{cid}/messages/{mid}").status_code == 404
        assert b.delete(f"/api/conversations/{cid}").status_code == 404
        assert len(a.get(f"/api/conversations/{cid}/messages").json()) == 2

        assert a.post("/auth/logout").status_code == 204
        assert a.get("/api/conversations").status_code == 401


def test_missing_acl_claim_cannot_search(oidc):
    folio = Folio()
    with client(folio) as c:
        login(c, oidc, sub="carol")  # no tenant claim: folio would drop a None filter and search everything
        cid = c.post("/api/conversations", json={}).json()["id"]
        assert c.post(f"/api/conversations/{cid}/messages", json={"content": "q"}).status_code == 403
        assert folio.requests == []


def test_oidc_requires_secret(env):
    env.setenv("FOLIO_APP_AUTH", "oidc")
    with pytest.raises(RuntimeError, match="FOLIO_APP_SECRET_KEY"):
        create_app()


def test_index_served(env):
    with client(Folio()) as c:
        r = c.get("/")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
