from __future__ import annotations

import pymupdf
import pytest

from folio.backends import resolve_backend
from folio.backends.base import PageStructure, StructureBackend, Word
from folio.backends.heuristic import HeuristicBackend
from folio.backends.http import HttpBackend
from tests._parse_helpers import FIX


def page_inputs(name: str, n: int = 0, dpi: int = 144):
    pg = pymupdf.open(FIX / name)[n]
    k = dpi / 72
    words = [Word(text=w[4], bbox=[w[0] * k, w[1] * k, w[2] * k, w[3] * k]) for w in pg.get_text("words")]
    return pg.get_pixmap(dpi=dpi).tobytes("png"), words


def test_heuristic_layout_types():
    png, words = page_inputs("contract_ko.pdf")
    s = HeuristicBackend().analyze(png, words)
    assert isinstance(HeuristicBackend(), StructureBackend)
    types = [r.type for r in s.regions]
    assert types.count("heading") == 3 and "paragraph" in types and s.ocr_words is None


def test_heuristic_table_cells():
    png, words = page_inputs("table.pdf")
    s = HeuristicBackend().analyze(png, words)
    assert len(s.tables) == 1
    t = s.tables[0]
    assert max(c.row + c.rowspan for c in t.cells) == 3 and max(c.col + c.colspan for c in t.cells) == 4
    assert [(c.row, c.col, c.colspan) for c in t.cells if c.colspan > 1] == [(0, 1, 2)]


def test_heuristic_ocr_needs_engine_or_clear_error():
    png, _ = page_inputs("digital.pdf")
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="rapidocr"):
            HeuristicBackend().analyze(png, None)


def test_server_and_http_backend_roundtrip():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from folio.backends.server import create_app

    client = fastapi_testclient.TestClient(create_app("heuristic"))
    assert client.get("/healthz").status_code == 200 and client.get("/readyz").status_code == 200
    png, words = page_inputs("table.pdf")
    remote = HttpBackend(url="http://testserver", client=client)
    got = remote.analyze(png, words)
    local = HeuristicBackend().analyze(png, words)
    assert isinstance(got, PageStructure) and got.model_dump() == local.model_dump()
    assert client.post("/v1/analyze", files={"image": ("p.png", png, "image/png")},
                       data={"words": "not json"}).status_code == 422


def test_http_backend_requires_url(monkeypatch):
    monkeypatch.delenv("FOLIO_STRUCTURE_URL", raising=False)
    with pytest.raises(ValueError):
        HttpBackend()


@pytest.mark.parametrize("alias,mod", [("docling", "docling"), ("ppstructure", "paddleocr")])
def test_optional_adapters_import_lazily(alias, mod):
    pytest.importorskip("folio.backends." + alias)
    try:
        __import__(mod)
        pytest.skip(f"{mod} installed")
    except ImportError:
        with pytest.raises(ImportError):
            resolve_backend(alias).analyze(page_inputs("digital.pdf")[0], None)
