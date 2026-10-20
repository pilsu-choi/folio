import os
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.integration
API = os.environ.get("FOLIO_E2E_API")
PDF = Path(__file__).parent.parent / "fixtures" / "contract_ko.pdf"


def _ingest_and_wait(c: httpx.Client) -> dict:
    r = c.post("/v1/ingest", files={"file": (PDF.name, PDF.read_bytes())}, data={"pipeline": "contracts"})
    assert r.status_code == 202
    jid = r.json()["job_id"]
    deadline = time.time() + 300
    while time.time() < deadline:
        job = c.get(f"/v1/jobs/{jid}").json()
        if job["status"] in ("succeeded", "partial", "failed"):
            return job
        time.sleep(2)
    raise AssertionError("job timeout")


def _count() -> int | None:
    url = os.environ.get("OPENSEARCH_URL")
    if not url:
        return None
    index = os.environ.get("FOLIO_E2E_INDEX", "contracts")
    return httpx.get(f"{url}/{index}/_count", timeout=10).json()["count"]


@pytest.mark.skipif(not API, reason="FOLIO_E2E_API not set")
@pytest.mark.skipif(not PDF.exists(), reason="fixture contract_ko.pdf missing")
def test_upload_succeeds_and_reupload_is_idempotent():
    with httpx.Client(base_url=API, timeout=30) as c:
        job = _ingest_and_wait(c)
        assert job["status"] == "succeeded", job
        before = _count()
        if before is not None:
            httpx.post(f"{os.environ['OPENSEARCH_URL']}/{os.environ.get('FOLIO_E2E_INDEX', 'contracts')}/_refresh")
            before = _count()
        job2 = _ingest_and_wait(c)
        assert job2["status"] == "succeeded" and job2["doc_id"] == job["doc_id"]
        if before is not None:
            httpx.post(f"{os.environ['OPENSEARCH_URL']}/{os.environ.get('FOLIO_E2E_INDEX', 'contracts')}/_refresh")
            assert _count() == before
