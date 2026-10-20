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
    (docs / "skip.csv").write_text("no")  # unsupported extension
    assert main(["--config-dir", d, "run", str(docs), "--pipeline", "p"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 1 and lines[0]["status"] == "succeeded"
    assert len(SINKS["default"]) == 2
    assert main(["--config-dir", d, "reprocess", "--from", "map", "--index", "idx"]) == 0


def test_run_source_uri(tmp_path, capsys):
    d = project(tmp_path)
    (tmp_path / "a.pdf").write_text("제1조\n")
    args = ["--config-dir", d, "run", str(tmp_path / "a.pdf"), "--pipeline", "p", "--source-uri"]
    assert main([*args, "https://dms.example/a"]) == 0
    assert main([*args, ""]) == 0  # empty (Airflow default) = no override
    sources = {json.loads(m.read_text())["source_uri"] for m in (tmp_path / "store").rglob("manifest.json")}
    assert sources == {"https://dms.example/a", str(tmp_path / "a.pdf")}


def test_run_failure_exit_code(tmp_path, capsys):
    d = project(tmp_path)
    assert main(["--config-dir", d, "run", "", "--pipeline", "p"]) == 2  # empty Airflow param
    assert json.loads(capsys.readouterr().out)["error"] == "empty path" and not SINKS.get("default")
    assert main(["--config-dir", d, "run", str(tmp_path / "missing.pdf"), "--pipeline", "p"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_eval_testset_run_compare(tmp_path, capsys):
    d = project(tmp_path)
    with open(f"{d}/pipelines/p.yaml", "a") as f:
        f.write("retrieval: {fn: 'tests.conftest:memory_retriever', mode: bm25}\n")
    src = tmp_path / "a.pdf"
    src.write_text("\n".join(f"제{i}조 보험금 청구 기한은 사고일로부터 {i}년 이내이며 서류를 갖추어 신청한다." for i in range(1, 9)))
    assert main(["--config-dir", d, "run", str(src), "--pipeline", "p"]) == 0
    capsys.readouterr()
    ts = tmp_path / "ts.json"
    assert main(["--config-dir", d, "eval", "testset", "--pipeline", "p", "--method", "heuristic", "--out", str(ts)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["items"] == 1 and json.loads(ts.read_text())[0]["evidence"][0]["page"] == 1
    results = []
    for _ in range(2):
        assert main(["--config-dir", d, "eval", "run", "--pipeline", "p", "--testset", str(ts), "--ks", "1,5",
                     "--out", str(tmp_path / "res")]) == 0
        r = json.loads(capsys.readouterr().out)
        assert r["unreachable"] == 0 and r["metrics"]["recall@5"] == 1.0 and set(r["by_type"]) == {"fact"}
        results.append(r["result"])
    assert main(["--config-dir", d, "eval", "compare", *results]) == 0
    cmp = json.loads(capsys.readouterr().out)
    assert cmp["n"] == 1 and cmp["metrics"]["recall@5"]["delta"] == 0.0
