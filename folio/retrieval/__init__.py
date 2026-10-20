"""Retrieval: one ``search`` interface over OpenSearch, Milvus and pgvector.

    r = create_retriever(sink_spec, config_dir=None, embed=None, retrieval=None, sink=None)
    r.search(query, k=None, filters=None, mode=None) -> list[Hit]

``retrieval`` is the pipeline YAML ``retrieval:`` (:class:`folio.contracts.RetrievalConfig` or a dict): defaults for
``mode``/``k``, BM25 ``fields`` weights (OpenSearch only), ``rrf_k``, ``candidates``, ``rerank`` and a custom ``fn``.
``filters`` is a dict ``{field: value}``: a scalar is equality, a list matches any element (so ``acl: ["user:a",
"group:x"]`` matches records whose ``acl`` contains any of them), a dict is a range over ``gt``/``gte``/``lt``/``lte``.
Hybrid = BM25 and vector legs fused client-side with Reciprocal Rank Fusion.
``embed`` is ``texts -> vectors`` (default :func:`folio.embed.http`).
"""

from __future__ import annotations

from folio._loader import load_callable
from folio.contracts import RetrievalConfig
from folio.errors import ConfigError
from folio.retrieval.base import Hit, Retriever, check_filters, rrf


def create_retriever(spec: dict, config_dir: str | None = None, embed=None, *,
                     retrieval: RetrievalConfig | dict | None = None, sink=None):
    rc = retrieval if isinstance(retrieval, RetrievalConfig) else RetrievalConfig.model_validate(retrieval or {})
    if sink is None:
        from folio.sinks import create_sink

        sink = create_sink(spec, config_dir=config_dir)
    if embed is None:
        from folio.embed import http as embed
    opts = rc.settings()
    if rc.rerank is not None:
        rerank_fn, rerank_opts = load_callable(rc.rerank.fn), rc.rerank.opts
        opts["rerank"] = lambda query, hits: rerank_fn(query, hits, **rerank_opts)
    if rc.fn:
        return load_callable(rc.fn)(sink, embed, **opts)
    kind = spec.get("type")
    if kind == "opensearch":
        from folio.retrieval.opensearch import OpenSearchRetriever as cls
    elif kind == "milvus":
        from folio.retrieval.milvus import MilvusRetriever as cls
    elif kind == "pgvector":
        from folio.retrieval.pgvector import PgVectorRetriever as cls
    else:
        raise ConfigError(f"no built-in retriever for sink {spec!r}; set retrieval.fn")
    return cls(sink, embed, **opts)


__all__ = ["Hit", "Retriever", "check_filters", "create_retriever", "rrf"]
