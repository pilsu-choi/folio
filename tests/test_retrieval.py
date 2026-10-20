import httpx
import pytest

from folio.retrieval import Hit, rrf
from folio.retrieval.base import Retriever
from folio.retrieval.milvus import build_filter
from folio.retrieval.opensearch import build_filters
from folio.retrieval.rerank import http_rerank


def H(i):
    return Hit(i, "d", 0.0, f"text {i}", {})


def test_rrf_fuses_and_orders():
    out = rrf([[H("a"), H("b"), H("c")], [H("c"), H("b")]], k=3)
    assert [h.chunk_id for h in out] == ["c", "b", "a"]
    assert out[0].score == pytest.approx(1 / 63 + 1 / 61)
    assert len(rrf([[H("a"), H("b")]], k=1)) == 1


def test_filter_builders():
    assert build_filters({"tenant_id": "t", "acl": ["u", "g"]}) == [{"term": {"tenant_id": "t"}}, {"terms": {"acl": ["u", "g"]}}]
    f = build_filter({"tenant_id": "t1", "acl": ["u:a", "g:x"], "page": 3, "doc_type": ["a", "b"]},
                     {"acl": {"type": "ARRAY"}, "tenant_id": {"type": "VARCHAR"}})
    assert f == 'tenant_id == "t1" and array_contains_any(acl, ["u:a", "g:x"]) and page == 3 and doc_type in ["a", "b"]'


def test_search_modes_and_none_filters():
    calls = []

    class R(Retriever):
        def _bm25(self, q, n, f):
            calls.append(("bm25", f))
            return [H("a"), H("b")]

        def _vector(self, v, n, f):
            calls.append(("vec", f))
            return [H("b")]

    r = R(None, lambda t: [[0.0]])
    assert [h.chunk_id for h in r.search("q", k=5, filters={"tenant_id": "t", "x": None})] == ["b", "a"]
    assert calls == [("bm25", {"tenant_id": "t"}), ("vec", {"tenant_id": "t"})]
    assert [h.chunk_id for h in r.search("q", mode="bm25")] == ["a", "b"]
    with pytest.raises(ValueError):
        r.search("q", mode="x")


def test_http_rerank_cohere_and_tei_shapes():
    hits = [H("a"), H("b"), H("c")]

    def handler(req):
        body = __import__("json").loads(req.content)
        assert str(req.url) == "http://rr/rerank" and body["documents"] == body["texts"] and body["top_n"] == 2
        return httpx.Response(200, json={"results": [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.5}]})

    out = http_rerank("q", hits, url="http://rr", model="m", top_n=2, transport=httpx.MockTransport(handler))
    assert [h.chunk_id for h in out] == ["c", "a"] and out[0].score == 0.9
    tei = httpx.MockTransport(lambda r: httpx.Response(200, json=[{"index": 1, "score": 0.7}, {"index": 0, "score": 0.1}]))
    assert [h.chunk_id for h in http_rerank("q", hits[:2], url="http://rr/rerank", transport=tei)] == ["b", "a"]
