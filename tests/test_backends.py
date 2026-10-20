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


class _SlowBackend:
    def __init__(self, device="cpu"):
        self.device = device
        self.gate = __import__("threading").Event()

    def warmup(self):
        if not self.gate.wait(5):
            raise RuntimeError("weights missing")

    def analyze(self, page_image, words):
        from folio.backends.base import PageStructure

        return PageStructure()


def test_server_readyz_waits_for_warmup():
    import time

    from fastapi.testclient import TestClient

    from folio.backends.server import create_app

    backend = _SlowBackend()
    client = TestClient(create_app(backend))
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["status"] == "loading"
    assert client.get("/healthz").status_code == 200
    backend.gate.set()
    for _ in range(50):
        if client.get("/readyz").status_code == 200:
            break
        time.sleep(0.02)
    assert client.get("/readyz").json()["status"] == "ready"


def test_server_device_from_env(monkeypatch):
    from folio.backends.server import _spec_from_env

    monkeypatch.setenv("FOLIO_STRUCTURE_BACKEND", "ppstructure")
    monkeypatch.setenv("FOLIO_STRUCTURE_DEVICE", "gpu")
    assert _spec_from_env() == {"fn": "ppstructure", "device": "gpu"}
    monkeypatch.setenv("FOLIO_STRUCTURE_CPU_THREADS", "4")
    assert _spec_from_env() == {"fn": "ppstructure", "device": "gpu", "cpu_threads": "4"}
    monkeypatch.setenv("FOLIO_STRUCTURE_BACKEND", "heuristic")
    assert _spec_from_env() == "heuristic"


def test_ppstructure_device_names():
    from folio.backends.ppstructure import PPStructureBackend

    assert PPStructureBackend(device="gpu").device == "gpu:0"
    assert PPStructureBackend(device="cpu").device == "cpu"
    assert PPStructureBackend(cpu_threads="4").pipeline_kwargs == {"cpu_threads": 4}


def test_ppstructure_numpy_results():
    """paddle returns cell boxes and OCR scores as numpy arrays; truth-testing them raised ValueError."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from folio.backends.ppstructure import PPStructureBackend

    res = {
        "layout_det_res": {"boxes": []},
        "table_res_list": [{"cell_box_list": np.array([[0, 0, 10, 10], [10, 0, 20, 10]], dtype=float),
                            "pred_html": "<table><tr><td>a</td><td>b</td></tr></table>"}],
        "overall_ocr_res": {"rec_texts": ["가", "나"], "rec_scores": np.array([0.9, 0.8]),
                            "rec_boxes": np.array([[0, 0, 5, 5], [5, 0, 10, 5]])},
    }

    class FakePipe:
        def predict(self, img):
            return [res]

    b = PPStructureBackend()
    b._pipeline = FakePipe()
    png, _ = page_inputs("digital.pdf")
    ps = b.analyze(png, None)
    assert ps.tables[0].bbox == [0, 0, 20, 10] and len(ps.tables[0].cells) == 2
    assert [w.conf for w in ps.ocr_words] == pytest.approx([0.9, 0.8])
