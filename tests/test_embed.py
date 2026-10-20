import math

import httpx
import pytest
from fastapi.testclient import TestClient

from folio.embed import http
from folio.embed.stub import create_app
from folio.errors import FolioError


def test_batches_order_and_auth():
    seen = []

    def handler(req: httpx.Request):
        body = __import__("json").loads(req.content)
        seen.append((str(req.url), req.headers.get("authorization"), body["model"], len(body["input"])))
        # return reversed to prove index ordering
        data = [{"index": i, "embedding": [float(t)]} for i, t in enumerate(body["input"])]
        return httpx.Response(200, json={"data": data[::-1]})

    out = http([str(i) for i in range(5)], url="http://e/v1/", model="m", batch_size=2, api_key="k",
               transport=httpx.MockTransport(handler))
    assert out == [[float(i)] for i in range(5)]
    assert [s[3] for s in seen] == [2, 2, 1]
    assert seen[0][:3] == ("http://e/v1/embeddings", "Bearer k", "m")


def test_retries_5xx_then_ok_and_gives_up():
    calls = {"n": 0}

    def flaky(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    assert http(["a"], url="http://e/v1", backoff=0, transport=httpx.MockTransport(flaky)) == [[1.0]]
    calls["n"] = -10
    with pytest.raises(FolioError):
        http(["a"], url="http://e/v1", backoff=0, transport=httpx.MockTransport(flaky))


def test_4xx_not_retried_and_connection_error_retried():
    n = {"c": 0}

    def bad(req):
        n["c"] += 1
        return httpx.Response(400)

    with pytest.raises(httpx.HTTPStatusError):
        http(["a"], url="http://e/v1", backoff=0, transport=httpx.MockTransport(bad))
    assert n["c"] == 1

    def boom(req):
        raise httpx.ConnectError("x")

    with pytest.raises(FolioError):
        http(["a"], url="http://e/v1", backoff=0, transport=httpx.MockTransport(boom))


def test_env_defaults(monkeypatch):
    monkeypatch.setenv("FOLIO_EMBED_URL", "http://env/v1")
    monkeypatch.setenv("FOLIO_EMBED_MODEL", "envm")

    def h(req):
        assert str(req.url) == "http://env/v1/embeddings"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5]}]})

    assert http(["a"], transport=httpx.MockTransport(h)) == [[0.5]]


def test_stub_server_deterministic_normalized(monkeypatch):
    monkeypatch.setenv("FOLIO_EMBED_STUB_DIM", "16")
    c = TestClient(create_app())
    assert c.get("/healthz").status_code == c.get("/readyz").status_code == 200
    assert c.get("/v1/models").json()["data"]
    a = c.post("/v1/embeddings", json={"input": ["hello world", "hello"], "model": "m"}).json()["data"]
    b = c.post("/v1/embeddings", json={"input": "hello world"}).json()["data"]
    assert len(a[0]["embedding"]) == 16 and a[0]["embedding"] == b[0]["embedding"]
    assert math.isclose(sum(x * x for x in a[0]["embedding"]), 1.0, rel_tol=1e-9)
    sim = lambda u, v: sum(x * y for x, y in zip(u, v))
    other = c.post("/v1/embeddings", json={"input": "zzz qqq"}).json()["data"][0]["embedding"]
    assert sim(a[0]["embedding"], a[1]["embedding"]) > sim(a[0]["embedding"], other)


def test_http_against_stub_app(monkeypatch):
    monkeypatch.setenv("FOLIO_EMBED_STUB_DIM", "8")
    app = TestClient(create_app())
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=app.post(r.url.path, content=r.content,
                                                              headers={"content-type": "application/json"}).content))
    out = http(["a", "b", "c"], url="http://stub/v1", batch_size=2, transport=transport)
    assert len(out) == 3 and len(out[0]) == 8
