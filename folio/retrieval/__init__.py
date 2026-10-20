"""Retrieval: one ``search`` interface over OpenSearch, Milvus and pgvector.

    r = create_retriever(sink_spec, config_dir=None, embed=None)
    r.search(query, k=10, filters=None, mode="hybrid" | "bm25" | "vector") -> list[Hit]

``filters`` is a dict ``{field: value}``; a list value matches any element (so ``acl: ["user:a", "group:x"]``
matches records whose ``acl`` contains any of them, and ``tenant_id`` is a plain equality filter).
Hybrid = BM25 and vector legs fused client-side with Reciprocal Rank Fusion.
``embed`` is ``texts -> vectors`` (default :func:`folio.embed.http`).
"""

from __future__ import annotations

from folio._loader import load_callable
from folio.retrieval.base import Hit, Retriever, rrf


def create_retriever(spec: dict, config_dir: str | None = None, embed=None):
    from folio.sinks import create_sink

    if "fn" in spec:
        opts = dict(spec)
        return load_callable(opts.pop("fn"))(**opts)
    sink = create_sink(spec, config_dir=config_dir)
    if embed is None:
        from folio.embed import http as embed
    kind = spec["type"]
    if kind == "opensearch":
        from folio.retrieval.opensearch import OpenSearchRetriever as cls
    elif kind == "milvus":
        from folio.retrieval.milvus import MilvusRetriever as cls
    else:
        from folio.retrieval.pgvector import PgVectorRetriever as cls
    return cls(sink, embed)


__all__ = ["Hit", "Retriever", "create_retriever", "rrf"]
