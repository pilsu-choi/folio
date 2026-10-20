import json
import subprocess

import pytest

from folio import eval as feval
from folio.chunk.strategies import adaptive, fixed
from folio.contracts import Document
from folio.errors import FolioError
from folio.eval import metrics, testset
from folio.retrieval.base import Hit


def _doc(doc_id, pages):
    """pages: list of block lists; a str is a paragraph, a list of rows is a table."""
    def block(b):
        if isinstance(b, list):
            return {"type": "table", "cells": b, "text_source": "native"}
        return {"type": "paragraph", "text": b, "text_source": "native"}
    return Document.model_validate({"doc_id": doc_id, "source_uri": f"{doc_id}.pdf", "content_hash": doc_id,
                                    "mime_type": "application/pdf",
                                    "pages": [{"page": i + 1, "blocks": [block(b) for b in bl]} for i, bl in enumerate(pages)]})


FILL = " ".join(f"보충 설명 문장 {i}번은 검색 평가를 위한 채움 텍스트입니다." for i in range(12))
D1_P1 = ["보험금은 사고일로부터 3년 이내에 청구하여야 한다.", FILL,
         [["구분", "2024년", "2025년"], ["매출", "120억", "150억"], ["영업이익", "10억", "12억"]]]
D1 = _doc("d1", [
    D1_P1,
    ["2025년 개정 약관에서는 청구 기한이 5년으로 늘어났다.", FILL],
])
D2 = _doc("d2", [["상대 회사의 2025년 매출은 90억 원으로 집계되었다.", FILL]])


def test_metrics():
    cov = [set(), {0}, {0, 1}]
    assert metrics.recall_at_k(cov, 2, 1) == 0.0 and metrics.recall_at_k(cov, 2, 2) == 0.5
    assert metrics.all_at_k(cov, 2, 2) == 0.0 and metrics.all_at_k(cov, 2, 3) == 1.0
    assert metrics.mrr(cov) == pytest.approx(0.5) and metrics.mrr([set()]) == 0.0
    assert metrics.ndcg_at_k([{0}], 1, 5) == 1.0
    assert metrics.ndcg_at_k([set(), {0}], 1, 5) == pytest.approx(0.6309, abs=1e-3)
    assert metrics.ndcg_at_k([], 0, 5) == 0.0


def test_testset_heuristic_deterministic():
    a, stats = testset.generate([D1, D2], pages=3, method="heuristic", seed=1)
    assert a == testset.generate([D1, D2], pages=3, method="heuristic", seed=1)[0] and stats["kept"]["fact"] == len(a)
    for it in a:
        ev = it["evidence"][0]
        assert it["type"] == "fact" and ev["text"] in testset.page_texts({"d1": D1, "d2": D2}[ev["doc_id"]])[ev["page"]]


def _fake_chat(items):
    def chat(messages, *, url, model, api_key, temperature, max_tokens, extra_body):
        assert url == "http://llm/v1" and model == "m" and extra_body == {"x": 1}
        return "<think>…</think>```json\n" + json.dumps(items, ensure_ascii=False) + "\n```"
    return chat


def q(t, question, *ev):
    return {"type": t, "question": question, "answer": "a", "evidence": [{"page": p, "quote": s} for p, s in ev]}


def _gen(monkeypatch, items, docs, **kw):
    monkeypatch.setattr(testset, "chat", _fake_chat(items))
    return testset.generate(docs, llm_url="http://llm/v1", model="m", extra_body={"x": 1}, min_chars=0, **kw)


def test_testset_llm_single_page_filters(monkeypatch):
    single = [
        q("fact", "보험금 청구 기한은?", ("A", "보험금은 사고일로부터  3년 이내에\n청구하여야 한다")),  # whitespace differs: ok
        q("fact", "지어낸 질문", ("A", "본문에 없는 문장을 인용했습니다.")),  # unlocated
        q("table", "표 헤더만?", ("A", "| 구분 | 2024년 | 2025년 |")),  # header only
        q("table", "2025년 매출은?", ("A", "| 매출 | 120억 | 150억 |")),
        q("multi_hop", "단일 페이지인데 multi", ("A", "보험금은 사고일로부터 3년 이내에")),  # wrong type
        q("fact", "보험금 청구 기한은?", ("A", "보험금은 사고일로부터 3년 이내에")),  # dup
    ]
    page1 = _doc("d1", [D1_P1])
    items, stats = _gen(monkeypatch, single, [page1], pages=5, pairs=0)
    assert stats["units"] == 1 and stats["failed_units"] == 0 and stats["kept"] == {"fact": 1, "table": 1}
    assert stats["unlocated"] == {"fact": 1} and stats["header_only"] == {"table": 1} and stats["dup"] == {"fact": 1}
    assert stats["wrong_type"] == {"multi_hop": 1}
    assert items[0]["evidence"][0] == {"doc_id": "d1", "page": 1, "text": "보험금은 사고일로부터 3년 이내에 청구하여야 한다",
                                       "start": 0, "end": 27}
    assert [it["qid"] for it in items] == ["q0000", "q0001"]


def test_testset_llm_page_pairs(monkeypatch):
    same = [
        q("temporal", "개정 전후 청구 기한 변화는?", ("A", "보험금은 사고일로부터 3년 이내에 청구"), ("B", "청구 기한이 5년으로 늘어났다")),
        q("multi_hop", "한쪽만 인용", ("A", "보험금은 사고일로부터 3년 이내에 청구")),  # one side only
    ]
    items, stats = _gen(monkeypatch, same, [D1, D2], pages=0, pairs=3, types=("temporal", "multi_hop"))
    assert stats["units"] == 2 and stats["one_side_only"] == {"multi_hop": 2}  # d2 has no same-doc partner
    assert [it["type"] for it in items] == ["temporal"] and stats["dup"] == {"temporal": 1}
    assert {(e["doc_id"], e["page"]) for e in items[0]["evidence"]} == {("d1", 1), ("d1", 2)}  # labels swapped: still found

    cross = [q("multi_document", "두 회사 2025년 매출 비교", ("A", "| 매출 | 120억 | 150억 |"), ("B", "2025년 매출은 90억 원으로"))]
    items, stats = _gen(monkeypatch, cross, [_doc("d1", [D1_P1]), D2], pages=0, pairs=1, types=("multi_document",))
    assert stats["units"] == 1 and len(items) == 1 and {e["doc_id"] for e in items[0]["evidence"]} == {"d1", "d2"}


def test_testset_llm_failures(monkeypatch):
    replies = iter(["not json", "still not json"] + ["[]"] * 10)  # first unit: malformed twice -> failed, rest ok
    monkeypatch.setattr(testset, "chat", lambda *a, **k: next(replies))
    items, stats = testset.generate([D1], pages=2, pairs=0, llm_url="http://llm/v1", min_chars=0, workers=1)
    assert items == [] and stats["units"] == 2 and stats["failed_units"] == 1
    monkeypatch.setattr(testset, "chat", lambda *a, **k: "not json")
    with pytest.raises(FolioError, match="every LLM call failed"):
        testset.generate([D1], pages=1, pairs=0, llm_url="http://llm/v1", min_chars=0)
    with pytest.raises(ValueError):
        testset.generate([D1], method="llm", types=("nope",), llm_url="http://x")


class Fake:
    """Returns, per query, the chunks whose content contains any of the listed substrings, in that order."""
    def __init__(self, chunks, order):
        self.chunks, self.order = chunks, order

    def search(self, query, k=10, filters=None, mode="hybrid"):
        hits = [c for s in self.order[query] for c in self.chunks if s in c["content"]]
        seen = list({c["chunk_id"]: c for c in hits}.values())
        return [Hit(c["chunk_id"], c["doc_id"], 1.0, c["content"], c) for c in seen][:k]


TS = [
    {"qid": "q0", "type": "fact", "query": "청구 기한",
     "evidence": [{"doc_id": "d1", "page": 1, "text": "보험금은 사고일로부터 3년 이내에 청구하여야 한다."}]},
    {"qid": "q1", "type": "temporal", "query": "개정",
     "evidence": [{"doc_id": "d1", "page": 1, "text": "보험금은 사고일로부터 3년 이내에 청구하여야 한다."},
                  {"doc_id": "d1", "page": 2, "text": "2025년 개정 약관에서는 청구 기한이 5년으로 늘어났다."}]},
]


@pytest.mark.parametrize("chunker", [lambda d: adaptive(d, max_tokens=512), lambda d: fixed(d, max_tokens=24, overlap=0)])
def test_run_is_chunking_independent(chunker):
    chunks = chunker(D1) + chunker(D2)
    r = feval.run(TS, Fake(chunks, {"청구 기한": ["사고일로부터"], "개정": ["개정 약관", "사고일로부터"]}), ks=(1, 5),
                  chunks=chunks)
    assert r["unreachable"] == [] and r["per_query"]["q0"]["recall@5"] == 1.0
    assert r["per_query"]["q1"]["all@5"] == 1.0 and r["by_type"]["temporal"]["n"] == 1
    miss = feval.run(TS, Fake(chunks, {"청구 기한": ["상대 회사"], "개정": ["개정 약관"]}), ks=(1, 5))
    assert miss["per_query"]["q0"]["recall@5"] == 0.0 and miss["per_query"]["q1"]["recall@5"] == 0.5
    assert miss["per_query"]["q1"]["all@5"] == 0.0 and "unreachable" not in miss


def test_page_mismatch_and_unreachable():
    chunks = adaptive(D1)
    wrong_page = [{**c, "pages": [9], "page": 9} for c in chunks]
    r = feval.run(TS[:1], Fake(wrong_page, {"청구 기한": ["사고일로부터"]}), ks=(5,), chunks=wrong_page)
    assert r["per_query"]["q0"]["recall@5"] == 0.0 and r["unreachable"] == ["q0"]
    no_page = [{k: v for k, v in c.items() if k not in ("page", "pages")} for c in chunks]  # store without page: doc only
    assert feval.run(TS[:1], Fake(no_page, {"청구 기한": ["사고일로부터"]}), ks=(5,))["metrics"]["recall@5"] == 1.0


def test_run_compare_save(tmp_path):
    chunks = adaptive(D1)
    good = feval.run(TS, Fake(chunks, {"청구 기한": ["사고일로부터"], "개정": ["개정 약관", "사고일로부터"]}), ks=(1, 5))
    bad = feval.run(TS, Fake(chunks, {"청구 기한": [], "개정": ["개정 약관"]}), ks=(1, 5))
    cmp = feval.compare(bad, good)
    assert cmp["n"] == 2 and cmp["metrics"]["recall@5"]["delta"] == pytest.approx(0.75)
    lo, hi = cmp["metrics"]["recall@5"]["ci95"]
    assert 0.5 <= lo <= hi <= 1.0 and cmp["by_type"]["fact"]["metrics"]["mrr"]["delta"] == 1.0
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
    r = feval.run([], Fake([], {}), config_dir=str(cfg))
    assert r["config_commit"] == sha
    assert feval.save_result(r, tmp_path / "res").name == f"{sha}.json"
