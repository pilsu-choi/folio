import httpx
import pytest

from folio.contracts import parse_config
from folio.errors import ConfigError
from folio.retrieval import Hit, check_filters, create_retriever, rrf
from folio.retrieval.base import Retriever
from folio.retrieval.milvus import MilvusRetriever, build_filter
from folio.retrieval.opensearch import OpenSearchRetriever, build_filters
from folio.retrieval.pgvector import PgVectorRetriever
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


def test_range_filters_all_stores():
    rng = {"page": {"gte": 2, "lt": 5}, "signed_at": {"gte": "2026-01-01"}}
    assert build_filters(rng) == [{"range": {"page": {"gte": 2, "lt": 5}}}, {"range": {"signed_at": {"gte": "2026-01-01"}}}]
    assert build_filter(rng, {}) == 'page >= 2 and page < 5 and signed_at >= "2026-01-01"'

    class PgSink:
        def introspect(self):
            return {"columns": {"page": "integer", "acl": "ARRAY", "metadata": "jsonb"}, "vector_column": "embedding"}

        def _col(self, k):
            return k

    r = PgVectorRetriever(PgSink(), None)
    where, params = r._where({"page": {"gte": 2}, "amount": {"lte": 100}, "signed_at": {"gt": "2026-01-01"}})
    assert [w.as_string(None) for w in where] == [
        '"page" >= %s', "(metadata->>%s::text)::numeric <= %s::numeric", "(metadata->>%s::text) > %s"]
    assert params == [2, "amount", 100, "signed_at", "2026-01-01"]
    with pytest.raises(ConfigError):
        r._where({"acl": {"gte": "a"}})


def test_check_filters_validation():
    assert check_filters({"a": 1, "b": None, "c": {"gt": 0}}) == {"a": 1, "c": {"gt": 0}}
    for bad in ({"a b": 1}, {'x") or 1==1 or ("': 1}, {"p": {"near": 1}}, {"p": {}}, {"p": {"gte": [1]}}, {"p": {"gte": None}}):
        with pytest.raises(ValueError):
            check_filters(bad)


def test_field_weights_opensearch_only():
    class OsSink:
        index, vector_field = "i", "embedding"

        def __init__(self):
            self.bodies = []
            self.client = self

        def search(self, index, body):
            self.bodies.append(body)
            return {"hits": {"hits": []}}

    sink = OsSink()
    OpenSearchRetriever(sink, None).search("q", mode="bm25")
    OpenSearchRetriever(sink, None, fields={"content": 1, "party": 2.5}).search("q", mode="bm25", filters={"page": {"lte": 3}})
    assert sink.bodies[0]["query"]["bool"]["must"] == [{"match": {"content": "q"}}]
    assert sink.bodies[1]["query"]["bool"] == {
        "must": [{"multi_match": {"query": "q", "fields": ["content^1", "party^2.5"]}}],
        "filter": [{"range": {"page": {"lte": 3}}}]}
    for cls in (MilvusRetriever, PgVectorRetriever):
        with pytest.raises(ConfigError, match="only 'content'"):
            cls(None, None, fields={"content": 1, "party": 2})


def test_settings_rerank_sees_candidates_then_cut_to_k():
    seen = {}

    class R(Retriever):
        def _bm25(self, q, n, f):
            seen["n"] = n
            return [H(str(i)) for i in range(n)]

        def _vector(self, v, n, f):
            return []

    def rerank(query, hits):
        seen["reranked"] = len(hits)
        return list(reversed(hits))

    r = R(None, lambda t: [[0.0]], mode="bm25", k=3, candidates=8, rerank=rerank)
    assert [h.chunk_id for h in r.search("q")] == ["7", "6", "5"]
    assert seen == {"n": 8, "reranked": 8}
    assert len(r.search("q", k=2)) == 2


def test_retrieval_config_and_create_retriever():
    cfg = parse_config({"sink": {"fn": "tests.conftest:memory_sink"}, "retrieval": {
        "fn": "tests.conftest:memory_retriever", "mode": "bm25", "k": 2, "fields": {"content": 1},
        "rerank": "tests.conftest:reverse_rerank", "required_filters": ["tenant_id"]}})
    assert cfg.retrieval.rerank.fn == "tests.conftest:reverse_rerank"
    from tests.conftest import memory_sink

    sink = memory_sink()
    sink.upsert([{"doc_id": "d", "chunk_id": f"c{i}", "content": f"alpha {i}"} for i in range(4)])
    r = create_retriever(cfg.sink, retrieval=cfg.retrieval, sink=sink)
    assert (r.mode, r.k) == ("bm25", 2) and [h.chunk_id for h in r.search("alpha")] == ["c3", "c2"]
    assert parse_config({"sink": {"type": "opensearch"}}).retrieval.fields == {"content": 1.0}
    for bad in ({"mode": "x"}, {"fields": {}}, {"k": 0}, {"unknown": 1}):
        with pytest.raises(ConfigError):
            parse_config({"sink": {"type": "opensearch"}, "retrieval": bad})
    with pytest.raises(ConfigError, match="retrieval.fn"):
        create_retriever({"fn": "tests.conftest:memory_sink"}, sink=sink)
