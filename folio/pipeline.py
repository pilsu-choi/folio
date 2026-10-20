"""Pipeline runner: parse -> chunk -> map -> embed -> index, with every stage a `module:function`."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from folio._loader import load_callable
from folio.contracts import (
    Document,
    PipelineConfig,
    StepSpec,
    load_config,
    make_doc_id,
    validate_chunk_record,
)
from folio.errors import ConfigError, ContractError, DimensionMismatchError
from folio.logging import log_event
from folio.storage import open_storage, read_uri

__all__ = ["PipelineResult", "delete_document", "load_config", "load_pipeline", "reprocess", "retriever", "run_pipeline",
           "search"]

STAGES = ["parse", "chunk", "map", "embed", "index"]
DEFAULT_MAP = "folio.map:common"
CONVERTED_PDF = "converted.pdf"
_DOC_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
Progress = Callable[[str, str, int], None]  # (stage, status running|done|failed, ms)

_SINKS: dict[str, Any] = {}
_RETRIEVERS: dict[str, Any] = {}


@dataclass
class PipelineResult:
    doc_id: str
    status: str = "succeeded"  # succeeded | partial | failed
    timings: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)
    errors: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def load_pipeline(name_or_path: str, config_dir: str | None = None) -> PipelineConfig:
    p = Path(name_or_path)
    if p.suffix in (".yaml", ".yml") or p.is_file():
        return load_config(p)
    root = Path(config_dir or os.environ.get("FOLIO_CONFIG_DIR") or os.getcwd())
    for ext in (".yaml", ".yml"):
        cand = root / "pipelines" / f"{name_or_path}{ext}"
        if cand.is_file():
            return load_config(cand)
    raise ConfigError(f"pipeline {name_or_path!r} not found under {root}/pipelines")


def _storage(cfg: PipelineConfig):
    return open_storage(os.environ.get("FOLIO_STORAGE_URI") or cfg.storage)


def _key(cfg: PipelineConfig, *parts: Any) -> str:
    return json.dumps([cfg.sink, cfg.config_dir, *parts], sort_keys=True, default=str)


def _sink(cfg: PipelineConfig):
    from folio.sinks import create_sink

    key = _key(cfg)
    if key not in _SINKS:
        _SINKS[key] = create_sink(cfg.sink, config_dir=cfg.config_dir)
    return _SINKS[key]


def _fn(spec: StepSpec) -> Callable:
    f = load_callable(spec.fn)
    opts = spec.opts
    return lambda *a: f(*a, **opts)


def _prepare_path(cfg: PipelineConfig) -> None:
    if cfg.config_dir and cfg.config_dir not in sys.path:
        sys.path.insert(0, cfg.config_dir)


def _ext(uri: str) -> str:
    return Path(urlparse(uri).path).suffix.lower()


def _check_mapped(records: list[dict], mapper: str) -> None:
    for i, rec in enumerate(records):
        try:
            validate_chunk_record(rec)
        except ContractError as exc:
            raise ContractError(f"mapper '{mapper}' produced an invalid chunk record (#{i}): {exc}") from exc


def _execute(
    cfg: PipelineConfig,
    uri: str | None,
    doc: Document | None,
    chunks: list[dict] | None,
    stop_after: str | None,
    source_uri: str | None,
    progress: Progress | None,
    storage,
) -> PipelineResult:
    if stop_after is not None and stop_after not in STAGES:
        raise ConfigError(f"stop_after must be one of {STAGES}, got {stop_after!r}")
    _prepare_path(cfg)
    timings: dict[str, int] = {}
    result = PipelineResult(doc_id="")

    def stage(name: str, fn: Callable[[], Any]) -> Any:
        if progress:
            progress(name, "running", 0)
        t0 = time.perf_counter()
        log_event("stage_start", logging.DEBUG, stage=name, doc_id=result.doc_id)
        try:
            out = fn()
        except Exception as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            timings[name] = ms
            if progress:
                progress(name, "failed", ms)
            log_event("stage_failed", logging.ERROR, stage=name, ms=ms, error=str(exc), doc_id=result.doc_id, exc_info=True)
            raise
        ms = int((time.perf_counter() - t0) * 1000)
        timings[name] = ms
        if progress:
            progress(name, "done", ms)
        log_event("stage", stage=name, ms=ms, doc_id=result.doc_id)
        return out

    def finish() -> PipelineResult:
        result.timings = timings
        errs = (doc.meta.get("errors") if doc else None) or []
        result.errors = list(errs)
        # model calls made by *this* run: a doc loaded from storage (reprocess) cost none
        calls = dict(doc.meta.get("calls", {})) if doc else {}
        result.calls = calls if parsed_now else {k: 0 for k in calls}
        if errs:
            result.status = "partial"
        return result

    parsed_now = doc is None
    if doc is None:
        assert uri is not None
        doc = stage("parse", lambda: _fn(cfg.parse)(uri))
        if not isinstance(doc, Document):
            doc = Document.model_validate(doc)
        if source_uri:
            doc.source_uri = source_uri
            doc.doc_id = make_doc_id(source_uri, doc.content_hash)
        result.doc_id = doc.doc_id
        base = f"{cfg.index}/{doc.doc_id}"
        storage.put_bytes(f"{base}/source{_ext(uri)}", read_uri(uri))
        # citation highlights need the exact PDF the bbox values refer to (re-conversion may differ)
        if doc._converted_pdf:
            storage.put_bytes(f"{base}/{CONVERTED_PDF}", doc._converted_pdf)
            doc.meta.setdefault("convert", {})["pdf_key"] = CONVERTED_PDF
        else:
            storage.delete(f"{base}/{CONVERTED_PDF}")
        storage.put_json(f"{base}/document.json", doc.model_dump(mode="json"))
        storage.put_json(
            f"{base}/manifest.json",
            {"source_uri": doc.source_uri, "origin_uri": uri, "mime_type": doc.mime_type, "pipeline": cfg.name,
             **({"converted_pdf": CONVERTED_PDF} if doc._converted_pdf else {})},
        )
    result.doc_id = doc.doc_id
    base = f"{cfg.index}/{doc.doc_id}"
    result.counts["pages"] = len(doc.pages)
    result.counts["blocks"] = sum(len(p.blocks) for p in doc.pages)
    if stop_after == "parse":
        return finish()

    if chunks is None:
        chunks = stage("chunk", lambda: _fn(cfg.chunk)(doc))
        storage.put_json(f"{base}/chunks.json", chunks)
    result.counts["chunks"] = len(chunks)
    if stop_after == "chunk":
        return finish()

    mapper = cfg.map or StepSpec(fn=DEFAULT_MAP)
    mfn = _fn(mapper)

    def do_map() -> list[dict]:
        out = [mfn(doc, dict(c)) for c in chunks]
        _check_mapped(out, mapper.fn)
        return out

    records = stage("map", do_map)
    if stop_after == "map":
        return finish()

    if cfg.embed is not None:
        efn = _fn(cfg.embed)

        def do_embed() -> None:
            texts = [r.get("contextualized_content") or r["content"] for r in records]
            vecs = efn(texts) if texts else []
            if len(vecs) != len(records):
                raise ContractError(f"embed returned {len(vecs)} vectors for {len(records)} chunks")
            for r, v in zip(records, vecs):
                r["embedding"] = list(v)

        stage("embed", do_embed)
    if stop_after == "embed":
        return finish()

    def do_index() -> None:
        sink = _sink(cfg)
        expected = sink.expected_dimension() if hasattr(sink, "expected_dimension") else None
        if expected and records and cfg.embed is not None:
            got = len(records[0].get("embedding", []))
            if got != expected:
                raise DimensionMismatchError(
                    f"embedding dimension {got} != sink schema dimension {expected} "
                    f"(index {cfg.index}); nothing was written"
                )
        sink.upsert(records)

    stage("index", do_index)
    storage.put_json(
        f"{base}/manifest.json",
        {**storage.get_json(f"{base}/manifest.json"), "chunks": len(records), "status": "indexed"},
    )
    return finish()


def run_pipeline(
    uri: str,
    cfg: PipelineConfig,
    stop_after: str | None = None,
    source_uri: str | None = None,
    progress: Progress | None = None,
) -> PipelineResult:
    """Run the pipeline for one document. Raises on failure; returns `partial` when pages failed."""
    storage = _storage(cfg)
    storage.ensure() if hasattr(storage, "ensure") else None
    return _execute(cfg, uri, None, None, stop_after, source_uri, progress, storage)


def _doc_ids(cfg: PipelineConfig, storage) -> list[str]:
    ids = {k.split("/")[1] for k in storage.list(f"{cfg.index}/") if k.count("/") >= 2}
    return sorted(ids)


def reprocess(
    cfg: PipelineConfig, start_from: str, doc_ids: list[str] | None = None, progress: Progress | None = None
) -> list[PipelineResult]:
    if start_from not in ("parse", "chunk", "map"):
        raise ConfigError("start_from must be parse|chunk|map")
    storage = _storage(cfg)
    out = []
    for doc_id in doc_ids or _doc_ids(cfg, storage):
        base = f"{cfg.index}/{doc_id}"
        manifest = storage.get_json(f"{base}/manifest.json")
        if start_from == "parse":
            src = next(k for k in storage.list(f"{base}/") if Path(k).name.startswith("source"))
            out.append(
                _execute(cfg, storage.uri(src), None, None, None, manifest.get("source_uri"), progress, storage)
            )
            continue
        doc = Document.model_validate(storage.get_json(f"{base}/document.json"))
        chunks = storage.get_json(f"{base}/chunks.json") if start_from == "map" else None
        out.append(_execute(cfg, None, doc, chunks, None, None, progress, storage))
    return out


def _doc_base(cfg: PipelineConfig, doc_id: str) -> str:
    """Storage prefix of a document; rejects ids that could escape it (`..`, `/`)."""
    if not _DOC_ID.fullmatch(doc_id):
        raise ValueError(f"invalid doc_id {doc_id!r}")
    return f"{cfg.index}/{doc_id}"


def delete_document(cfg: PipelineConfig, doc_id: str) -> None:
    base = _doc_base(cfg, doc_id)
    _sink(cfg).delete(doc_id)
    _storage(cfg).delete(base)


def document_pdf(cfg: PipelineConfig, doc_id: str) -> bytes | None:
    """The PDF that the document's pages/bbox refer to: the converted PDF, or the source if it is a PDF. None if absent."""
    base, storage = _doc_base(cfg, doc_id), _storage(cfg)
    for key in (f"{base}/{CONVERTED_PDF}", f"{base}/source.pdf"):
        if storage.exists(key):
            return storage.get_bytes(key)
    return None


def _no_embed(texts: list[str]):
    raise ConfigError("vector/hybrid search needs an embed step (pipeline has embed: null); use mode bm25")


def retriever(cfg: PipelineConfig):
    """The pipeline's retriever: its sink, its ``embed`` step (same model as ingest) and ``retrieval:`` settings."""
    from folio.retrieval import create_retriever

    key = _key(cfg, cfg.embed, cfg.retrieval)
    if key not in _RETRIEVERS:
        _prepare_path(cfg)
        embed = _fn(cfg.embed) if cfg.embed is not None else _no_embed
        _RETRIEVERS[key] = create_retriever(cfg.sink, cfg.config_dir, embed, retrieval=cfg.retrieval, sink=_sink(cfg))
    return _RETRIEVERS[key]


def search(cfg: PipelineConfig, query: str, k: int | None = None, filters: dict | None = None,
           mode: str | None = None) -> list:
    """Search the pipeline's index. Raises ``ValueError`` for a missing ``retrieval.required_filters`` field."""
    from folio.retrieval import check_filters

    filters = check_filters(filters)
    missing = [f for f in cfg.retrieval.required_filters if f not in filters]
    if missing:
        raise ValueError(f"missing required filters: {missing}")
    return retriever(cfg).search(query, k=k, filters=filters, mode=mode)
