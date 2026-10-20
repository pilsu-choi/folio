import json
import subprocess

import httpx
import pytest

from folio import eval as feval
from folio.eval import metrics, testset
from folio.retrieval.base import Hit


def test_metrics():
    assert metrics.recall_at_k(["a", "b"], ["x", "a"], 2) == 0.5
    assert metrics.recall_at_k(["a"], ["x", "a"], 1) == 0.0
    assert metrics.mrr(["a"], ["x", "y", "a"]) == pytest.approx(1 / 3)
    assert metrics.mrr(["a"], ["x"]) == 0.0
    assert metrics.ndcg_at_k(["a"], ["a"], 5) == 1.0
    assert metrics.ndcg_at_k(["a"], ["x", "a"], 5) == pytest.approx(0.6309, abs=1e-3)
    assert metrics.ndcg_at_k([], ["a"], 5) == 0.0


CHUNKS = [{"chunk_id": f"c{i}", "doc_id": "d", "content": f"Topic{i} explains the quantum{i} widget calibration. Other filler."}
          for i in range(10)] + [{"chunk_id": "tiny", "doc_id": "d", "content": "hi"}]


def test_testset_heuristic_deterministic():
    a = testset.generate(CHUNKS, 4, seed=1)
    assert a == testset.generate(CHUNKS, 4, seed=1) and len(a) == 4
    assert all(t["relevant_chunk_ids"][0] != "tiny" and t["doc_id"] == "d" for t in a)
    assert any(f"quantum{t['relevant_chunk_ids'][0][1:]}" in t["query"] for t in a)


def test_testset_llm(monkeypatch):
    def fake_post(url, **kw):
        assert url == "http://llm/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": '"my query"'}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    ts = testset.generate(CHUNKS, 2, method="llm", llm_url="http://llm/v1", model="m")
    assert [t["query"] for t in ts] == ["my query"] * 2


class Fake:
    def __init__(self, order):
        self.order = order

    def search(self, query, k=10, filters=None, mode="hybrid"):
        return [Hit(c, "d", 1.0, "", {}) for c in self.order[query]][:k]


def test_run_compare_save(tmp_path):
    ts = [{"query": "q1", "relevant_chunk_ids": ["a"], "doc_id": "d"}, {"query": "q2", "relevant_chunk_ids": ["b"], "doc_id": "d"}]
    good = feval.run(ts, Fake({"q1": ["a"], "q2": ["b"]}), ks=(1, 5))
    bad = feval.run(ts, Fake({"q1": ["x", "a"], "q2": ["x"]}), ks=(1, 5))
    assert good["metrics"]["recall@1"] == 1.0 and bad["metrics"]["recall@5"] == 0.5 and bad["config_commit"] is None
    cmp = feval.compare(bad, good)
    assert cmp["metrics"]["mrr"]["delta"] == pytest.approx(0.75) and cmp["metrics"]["recall@1"]["delta"] == 1.0
    p = feval.save_result(good, tmp_path / "out")
    assert p.stem == good["timestamp"] and json.loads(p.read_text())["n"] == 2


def test_config_commit(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "p.yaml").write_text("a: 1")
    git = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    sha = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    r = feval.run([], Fake({}), config_dir=str(cfg))
    assert r["config_commit"] == sha
    assert feval.save_result(r, tmp_path / "res").name == f"{sha}.json"
