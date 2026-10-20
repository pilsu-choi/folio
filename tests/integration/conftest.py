"""Container fixtures. Reuses running ``folio-it-*`` containers; starts missing ones; removes only what it started
(set FOLIO_IT_KEEP=1 to leave them running). Skips when docker is unavailable."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parent
OS_URL, PG_DSN, MILVUS_URI = "http://localhost:19200", "postgresql://postgres:folio@localhost:15432/app", "http://localhost:19530"
_started: list[str] = []


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def _running(name: str) -> bool:
    return _docker("ps", "-q", "-f", f"name=^{name}$").stdout.strip() != ""


def _ensure(name: str, run_args: list[str], ready, timeout: float = 180) -> None:
    if shutil.which("docker") is None or _docker("info").returncode != 0:
        pytest.skip("docker unavailable")
    if not _running(name):
        _docker("rm", "-f", name)  # a stopped container of ours with the same name
        r = _docker("run", "-d", "--name", name, *run_args)
        if r.returncode != 0:
            pytest.skip(f"cannot start {name}: {r.stderr.strip()[:200]}")
        _started.append(name)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if ready():
                return
        except Exception:
            pass
        time.sleep(2)
    pytest.fail(f"{name} not ready after {timeout}s")


@pytest.fixture(scope="session")
def opensearch_url():
    _ensure("folio-it-opensearch", [
        "-p", "19200:9200", "-e", "discovery.type=single-node", "-e", "DISABLE_SECURITY_PLUGIN=true",
        "-e", "DISABLE_INSTALL_DEMO_CONFIG=true", "-e", "OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m",
        "opensearchproject/opensearch:2.19.1"], lambda: httpx.get(OS_URL + "/_cluster/health").status_code == 200)
    return OS_URL


@pytest.fixture(scope="session")
def pg_dsn():
    import psycopg

    _ensure("folio-it-pgvector", ["-p", "15432:5432", "-e", "POSTGRES_PASSWORD=folio", "-e", "POSTGRES_DB=app",
                                  "pgvector/pgvector:pg16"], lambda: psycopg.connect(PG_DSN, connect_timeout=2).close() or True)
    with psycopg.connect(PG_DSN, autocommit=True) as c:  # App-side setup, not done by the sink
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return PG_DSN


@pytest.fixture(scope="session")
def milvus_uri(tmp_path_factory):
    cfg = tmp_path_factory.mktemp("milvus")
    (cfg / "embedEtcd.yaml").write_text(
        "listen-client-urls: http://0.0.0.0:2379\nadvertise-client-urls: http://0.0.0.0:2379\n"
        "quota-backend-bytes: 4294967296\nauto-compaction-mode: revision\nauto-compaction-retention: '1000'\n")
    (cfg / "user.yaml").write_text("common:\n  storageType: local\n")
    _ensure("folio-it-milvus", [
        "-p", "19530:19530", "-p", "19091:9091", "-v", f"{cfg}/embedEtcd.yaml:/milvus/configs/embedEtcd.yaml",
        "-v", f"{cfg}/user.yaml:/milvus/configs/user.yaml", "-e", "DEPLOY_MODE=STANDALONE", "-e", "ETCD_USE_EMBED=true",
        "-e", "ETCD_DATA_DIR=/var/lib/milvus/etcd", "-e", "ETCD_CONFIG_PATH=/milvus/configs/embedEtcd.yaml",
        "-e", "COMMON_STORAGETYPE=local", "milvusdb/milvus:v2.6.25", "milvus", "run", "standalone"],
        lambda: httpx.get("http://localhost:19091/healthz").text.strip() == "OK")
    return MILVUS_URI


@pytest.fixture
def config_dir(tmp_path):
    shutil.copytree(HERE.parent / "schemas", tmp_path / "schemas")
    return tmp_path


def pytest_sessionfinish(session, exitstatus):
    if not os.environ.get("FOLIO_IT_KEEP"):
        for name in _started:
            _docker("rm", "-f", name)
