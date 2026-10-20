import io
import json
import logging

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("celery")
pytest.importorskip("multipart")

from folio.logging import configure
from tests.test_service import client  # noqa: F401


@pytest.fixture
def lines():
    """JSON lines emitted on the `service` logger (propagation is off, so caplog does not see them)."""
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.getLogger("service").handlers[0].formatter)
    log = logging.getLogger("service")
    log.addHandler(h)
    yield lambda: [json.loads(x) for x in buf.getvalue().splitlines()]
    log.removeHandler(h)
    configure("INFO")


def test_request_id_header_echo_and_generated(client, lines):  # noqa: F811
    assert client.get("/healthz", headers={"X-Request-ID": "abc"}).headers["X-Request-ID"] == "abc"
    assert len(client.get("/healthz").headers["X-Request-ID"]) >= 8


def test_one_info_line_per_request_and_healthz_quiet(client, lines):  # noqa: F811
    client.get("/healthz")
    assert not [r for r in lines() if r["event"] == "http_request"]
    client.get("/v1/jobs/nope", headers={"X-Request-ID": "r1"})
    reqs = [r for r in lines() if r["event"] == "http_request"]
    assert len(reqs) == 1
    assert reqs[0]["status"] == 404 and reqs[0]["request_id"] == "r1" and reqs[0]["level"] == "INFO"
    assert reqs[0]["path"] == "/v1/jobs/nope" and "ms" in reqs[0]


def test_unhandled_exception_logged_as_error(client, lines):  # noqa: F811
    from service import api

    def boom():
        raise RuntimeError("kaboom")

    api.app.add_api_route("/_boom", boom)
    try:
        r = client.get("/_boom", headers={"X-Request-ID": "e1"})
    finally:
        api.app.router.routes.pop()
    assert r.status_code == 500 and r.headers["X-Request-ID"] == "e1"
    err = [x for x in lines() if x["level"] == "ERROR"]
    assert err and "RuntimeError" in err[0]["exc"] and err[0]["request_id"] == "e1"


def test_cli_log_level(monkeypatch):
    from folio import cli

    monkeypatch.delenv("FOLIO_LOG_LEVEL", raising=False)
    monkeypatch.setattr(cli, "cmd_init", lambda a: 0)
    try:
        assert cli.main(["--log-level", "DEBUG", "init"]) == 0
        assert logging.getLogger("folio").level == logging.DEBUG
        assert cli.os.environ["FOLIO_LOG_LEVEL"] == "DEBUG"
        assert cli.main(["--log-level", "bogus", "init"]) == 0
        assert logging.getLogger("folio").level == logging.INFO
    finally:
        cli.os.environ.pop("FOLIO_LOG_LEVEL", None)
        configure("INFO")
