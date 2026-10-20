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
