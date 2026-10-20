"""Shared fakes for pipeline/service tests (no model, no network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folio.contracts import Block, Document, Page, content_hash, make_chunk_id, make_doc_id, parse_config
from folio.retrieval import Hit, Retriever
from folio.storage import read_uri

CALLS = {"parse": 0, "chunk": 0, "embed": 0}
SINKS: dict[str, dict] = {}  # name -> {chunk_id: record}
DIM = {"value": 4}
FAIL_PAGES = {"on": False}


def fake_parse(uri: str, **opts) -> Document:
    CALLS["parse"] += 1
    data = read_uri(uri)
    ch = content_hash(data)
    blocks = [
        Block(type="paragraph", text=t, bbox=[0, 0, 1, 1], text_source="pdf_text", section_path=["제1조"])
        for t in data.decode("utf-8", "ignore").split("\n") if t.strip()
    ]
    meta = {"calls": {"structure": 1, "ocr": 0, "vlm": 0}}
    if FAIL_PAGES["on"]:
        meta["errors"] = [{"page": 2, "error": "boom"}]
    return Document(
        doc_id=make_doc_id(uri, ch), source_uri=uri, content_hash=ch, mime_type="application/pdf",
        pages=[Page(page=1, blocks=blocks)], meta=meta,
    )


def fake_chunk(doc: Document, **opts) -> list[dict]:
    CALLS["chunk"] += 1
    out = []
    for i, (page, b) in enumerate(doc.iter_blocks()):
        out.append({
            "doc_id": doc.doc_id, "chunk_id": make_chunk_id(doc.doc_id, i, b.text), "content": b.text,
            "page": page.page, "section_path": list(b.section_path), "source_uri": doc.source_uri,
        })
    return out


def fake_embed(texts: list[str], **opts) -> list[list[float]]:
    CALLS["embed"] += 1
    dim = opts.get("dim", DIM["value"])
    return [[float(len(t) % 7)] * dim for t in texts]


def identity_map(doc, chunk):
    return chunk


def tag_map(doc, chunk):
    return {**chunk, "tag": "x"}


def bad_map(doc, chunk):
    chunk = dict(chunk)
    del chunk["content"]
    return chunk


class MemorySink:
    def __init__(self, name="default", dim=None):
        self.rows = SINKS.setdefault(name, {})
        self.dim = dim

    def upsert(self, records):
        for d in {r["doc_id"] for r in records}:
            for k in [k for k, v in self.rows.items() if v["doc_id"] == d]:
                del self.rows[k]
        for r in records:
            self.rows[r["chunk_id"]] = r

    def delete(self, doc_id):
        for k in [k for k, v in self.rows.items() if v["doc_id"] == doc_id]:
            del self.rows[k]

    def expected_dimension(self):
        return self.dim

    def count(self, doc_id=None):
        return len([1 for v in self.rows.values() if doc_id in (None, v["doc_id"])])

    def apply_schema(self, reindex=False):
        return {"created": True}

    def ping(self):
        return True


def memory_sink(**opts):
    return MemorySink(opts.get("name", "default"), opts.get("dim"))


class MemoryRetriever(Retriever):
    """Substring match over MemorySink rows with equality filters; the vector leg is empty."""

    def _bm25(self, query, n, filters):
        rows = [r for r in self.sink.rows.values()
                if any(w in r["content"] for w in query.split()) and all(r.get(k) == v for k, v in filters.items())]
        return [Hit(r["chunk_id"], r["doc_id"], 1.0, r["content"], r) for r in rows][:n]

    def _vector(self, vector, n, filters):
        return []


def memory_retriever(sink, embed, **opts):
    return MemoryRetriever(sink, embed, **opts)


def reverse_rerank(query, hits, **opts):
    return list(reversed(hits))


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for k in CALLS:
        CALLS[k] = 0
    SINKS.clear()
    DIM["value"] = 4
    FAIL_PAGES["on"] = False
    from folio import pipeline

    pipeline._SINKS.clear()
    monkeypatch.delenv("FOLIO_STORAGE_URI", raising=False)


@pytest.fixture
def make_cfg(tmp_path):
    def make(**over):
        data = {
            "name": "t",
            "parse": "tests.conftest:fake_parse",
            "chunk": "tests.conftest:fake_chunk",
            "map": "tests.conftest:identity_map",
            "embed": "tests.conftest:fake_embed",
            "sink": {"fn": "tests.conftest:memory_sink", "index": "idx"},
            "storage": str(tmp_path / "store"),
        }
        data.update(over)
        return parse_config(data)

    return make


@pytest.fixture
def sample(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_text("제1조 목적\n제2조 정의\n제3조 기간\n", encoding="utf-8")
    return str(p)
