"""Sinks: ``create_sink(spec, config_dir=None)`` builds OpenSearch / Milvus / pgvector / custom sinks."""

from __future__ import annotations

from folio._loader import load_callable
from folio.errors import ConfigError


def create_sink(spec: dict, config_dir: str | None = None):
    """``spec`` is the pipeline ``sink`` mapping: ``{type: opensearch|milvus|pgvector, ...}`` or ``{fn: "module:factory", ...}``."""
    opts = dict(spec)
    if "fn" in opts:
        return load_callable(opts.pop("fn"))(**opts)
    kind = opts.pop("type", None)
    if kind == "opensearch":
        from folio.sinks.opensearch import OpenSearchSink as cls
    elif kind == "milvus":
        from folio.sinks.milvus import MilvusSink as cls
    elif kind == "pgvector":
        from folio.sinks.pgvector import PgVectorSink as cls
    else:
        raise ConfigError(f"unknown sink type {kind!r}; expected opensearch, milvus, pgvector or fn")
    return cls(config_dir=config_dir, **opts)


__all__ = ["create_sink"]
