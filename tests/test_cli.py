import json

from folio.cli import main
from tests.conftest import SINKS

YAML = """\
parse: tests.conftest:fake_parse
chunk: tests.conftest:fake_chunk
map: tests.conftest:identity_map
embed: tests.conftest:fake_embed
sink: {{fn: 'tests.conftest:memory_sink', index: idx}}
storage: {storage}
"""


def project(tmp_path):
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "pipelines" / "p.yaml").write_text(YAML.format(storage=tmp_path / "store"))
    return str(tmp_path)


def test_init_is_idempotent(tmp_path, capsys, monkeypatch):
    d = project(tmp_path)
    monkeypatch.setenv("FOLIO_DATABASE_URL", f"sqlite:///{tmp_path}/j.db")
    for _ in range(2):
        assert main(["--config-dir", d, "init"]) == 0
    assert main(["--config-dir", d, "cli", "init", "--pipeline", "p"]) == 0
    assert (tmp_path / "store").is_dir() and (tmp_path / "j.db").exists()
    assert main(["--config-dir", d, "schema", "apply"]) == 0


def test_run_directory_and_reprocess(tmp_path, capsys):
    d = project(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.pdf").write_text("제1조\n제2조\n")
    (docs / "skip.txt").write_text("no")
    assert main(["--config-dir", d, "run", str(docs), "--pipeline", "p"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 1 and lines[0]["status"] == "succeeded"
    assert len(SINKS["default"]) == 2
    assert main(["--config-dir", d, "reprocess", "--from", "map", "--index", "idx"]) == 0


def test_run_failure_exit_code(tmp_path, capsys):
    d = project(tmp_path)
    assert main(["--config-dir", d, "run", str(tmp_path / "missing.pdf"), "--pipeline", "p"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
