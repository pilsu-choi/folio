import pytest

pytest.importorskip("fastapi")
pytest.importorskip("celery")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

from tests.conftest import FAIL_PAGES, SINKS


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "pipelines" / "contracts.yaml").write_text(
        "parse: tests.conftest:fake_parse\nchunk: tests.conftest:fake_chunk\n"
        "map: tests.conftest:identity_map\nembed: tests.conftest:fake_embed\n"
        "sink: {fn: 'tests.conftest:memory_sink', index: idx}\n"
        "retrieval: {fn: 'tests.conftest:memory_retriever'}\n"
        f"storage: {tmp_path}/store\n")
    monkeypatch.setenv("FOLIO_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("FOLIO_DATABASE_URL", f"sqlite:///{tmp_path}/jobs.db")
    monkeypatch.setenv("FOLIO_STORAGE_URI", str(tmp_path / "store"))
    from service.worker import celery_app

    celery_app.conf.update(task_always_eager=True, task_eager_propagates=False)
    with TestClient(__import__("service.api", fromlist=["app"]).app) as c:
        yield c
    celery_app.conf.update(task_always_eager=False)


def upload(client, data="제1조\n제2조\n".encode(), name="c.pdf"):
    r = client.post("/v1/ingest", files={"file": (name, data)}, data={"pipeline": "contracts"})
    assert r.status_code == 202
    return r.json()["job_id"]


def test_ingest_to_succeeded_and_idempotent(client):
    job = client.get(f"/v1/jobs/{upload(client)}").json()
    assert job["status"] == "succeeded" and job["doc_id"]
    assert set(job["stages"]) == {"parse", "chunk", "map", "embed", "index"}
    assert all(s["status"] == "done" for s in job["stages"].values())
    n = len(SINKS["default"])
    job2 = client.get(f"/v1/jobs/{upload(client)}").json()
    assert job2["doc_id"] == job["doc_id"] and len(SINKS["default"]) == n == 2


def test_partial_and_failed_with_error_message(client):
    FAIL_PAGES["on"] = True
    job = client.get(f"/v1/jobs/{upload(client)}").json()
    assert job["status"] == "partial" and "boom" in job["error"]
    FAIL_PAGES["on"] = False
    r = client.post("/v1/ingest", files={"file": ("x.pdf", b"a")}, data={"pipeline": "nope"})
    assert r.status_code == 400
    # failure inside pipeline: dimension mismatch via bad mapper is simplest -> remove source
    job_id = upload(client, name="d.pdf")
    from service.jobs import JobStore
    from service.worker import ingest

    JobStore().update(job_id, source_uri="/nonexistent/file.pdf")
    ingest.apply(args=[job_id])
    job = client.get(f"/v1/jobs/{job_id}").json()
    assert job["status"] == "failed" and "nonexistent" in job["error"]


def test_health_ready_delete_and_404(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200
    assert client.get("/v1/jobs/none").status_code == 404
    job = client.get(f"/v1/jobs/{upload(client)}").json()
    assert client.delete(f"/v1/documents/{job['doc_id']}?pipeline=contracts").status_code == 200
    assert SINKS["default"] == {}


def test_readyz_503_when_db_down(client, monkeypatch):
    from service import api

    monkeypatch.setattr(api.JobStore, "ping", lambda self: (_ for _ in ()).throw(RuntimeError("down")))
    r = client.get("/readyz")
    assert r.status_code == 503 and "down" in r.json()["checks"]["db"]


def test_search_endpoint(client, tmp_path):
    job = client.get(f"/v1/jobs/{upload(client)}").json()
    r = client.post("/v1/search", json={"query": "제1조", "pipeline": "contracts", "k": 5})
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert len(hits) == 1 and hits[0]["doc_id"] == job["doc_id"] and hits[0]["content"] == "제1조"
    assert "embedding" not in hits[0]["record"] and hits[0]["record"]["chunk_id"] == hits[0]["chunk_id"]
    assert client.post("/v1/search", json={"query": "제1조", "filters": {"doc_id": "other"}}).json()["hits"] == []
    assert client.post("/v1/search", json={"query": "x", "pipeline": "nope"}).status_code == 400
    assert client.post("/v1/search", json={"query": "x", "k": 0}).status_code == 422
    assert client.post("/v1/search", json={"query": "x", "mode": "fuzzy"}).status_code == 400
    assert client.post("/v1/search", json={"query": "x", "filters": {"page": {"near": 1}}}).status_code == 400
    assert client.post("/v1/search", json={"query": "x", "filters": {"a b": 1}}).status_code == 400
    # required_filters: missing or null tenant_id is rejected
    (tmp_path / "pipelines" / "secure.yaml").write_text(
        (tmp_path / "pipelines" / "contracts.yaml").read_text().replace(
            "retrieval: {fn: 'tests.conftest:memory_retriever'}",
            "retrieval: {fn: 'tests.conftest:memory_retriever', required_filters: [tenant_id]}"))
    for filters in ({}, {"tenant_id": None}):
        r = client.post("/v1/search", json={"query": "제1조", "pipeline": "secure", "filters": filters})
        assert r.status_code == 400 and "tenant_id" in r.json()["detail"]
    r = client.post("/v1/search", json={"query": "제1조", "pipeline": "secure", "filters": {"tenant_id": "t1"}})
    assert r.status_code == 200 and r.json()["hits"] == []


def test_chat_endpoint(client, tmp_path):
    upload(client)
    assert client.post("/v1/chat", json={"messages": [{"role": "user", "content": "제1조"}]}).status_code == 400
    (tmp_path / "pipelines" / "chat.yaml").write_text(
        (tmp_path / "pipelines" / "contracts.yaml").read_text()
        + "generation: {chat: 'tests.test_generation:fake_chat', rewrite: false}\n")
    msgs = [{"role": "user", "content": "제1조"}, {"role": "assistant", "content": "a [1]"},
            {"role": "user", "content": "제1조"}]
    r = client.post("/v1/chat", json={"messages": msgs, "pipeline": "chat", "k": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "제1조" and body["answer"].endswith("[9].")
    assert body["hits"][0]["content"] == "제1조" and body["citations"][0]["chunk_id"] == body["hits"][0]["chunk_id"]
    assert "embedding" not in body["hits"][0]["record"]
    system = [{"role": "system", "content": "x"}, {"role": "user", "content": "q"}]
    assert client.post("/v1/chat", json={"messages": system, "pipeline": "chat"}).status_code == 422
    assert client.post("/v1/chat", json={"messages": [], "pipeline": "chat"}).status_code == 422
    last_bot = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
    assert client.post("/v1/chat", json={"messages": last_bot, "pipeline": "chat"}).status_code == 400
    (tmp_path / "pipelines" / "down.yaml").write_text(
        (tmp_path / "pipelines" / "chat.yaml").read_text().replace("fake_chat", "broken_chat"))
    r = client.post("/v1/chat", json={"messages": msgs[:1], "pipeline": "down"})
    assert r.status_code == 502 and "llm down" in r.json()["detail"]
