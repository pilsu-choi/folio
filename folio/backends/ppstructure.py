"""PP-StructureV3 (PaddleOCR >= 3.2) StructureBackend. Optional dependency, imported lazily."""

from __future__ import annotations

import threading
from html.parser import HTMLParser

from folio.backends.base import PageStructure, Region, Table, TableCell, Word

# layout label -> (region type, heading level)
_LABELS: dict[str, tuple[str, int | None]] = {
    "doc_title": ("heading", 1), "paragraph_title": ("heading", 2),
    "text": ("paragraph", None), "content": ("paragraph", None), "abstract": ("paragraph", None),
    "aside_text": ("paragraph", None), "vertical_text": ("paragraph", None),
    "table": ("table", None),
    "image": ("figure", None), "figure": ("figure", None), "chart": ("figure", None),
    "seal": ("figure", None),
    "figure_title": ("caption", None), "chart_title": ("caption", None),
    "table_title": ("caption", None), "caption": ("caption", None),
    "header": ("header", None), "header_image": ("header", None),
    "footer": ("footer", None), "footer_image": ("footer", None),
    "footnote": ("footer", None), "number": ("footer", None),
    "list": ("list", None), "reference": ("list", None), "reference_content": ("list", None),
    "formula": ("formula", None), "display_formula": ("formula", None),
    "inline_formula": ("formula", None), "formula_number": ("formula", None),
    "algorithm": ("paragraph", None),
}


class _GridParser(HTMLParser):
    """Collect (row, col, rowspan, colspan) per <td>/<th> in document order, honoring spans."""

    def __init__(self) -> None:
        super().__init__()
        self.cells: list[tuple[int, int, int, int]] = []
        self._row = -1
        self._col = 0
        self._busy: set[tuple[int, int]] = set()

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row += 1
            self._col = 0
        elif tag in ("td", "th"):
            a = dict(attrs)
            rs = max(int(a.get("rowspan") or 1), 1)
            cs = max(int(a.get("colspan") or 1), 1)
            while (self._row, self._col) in self._busy:
                self._col += 1
            self.cells.append((self._row, self._col, rs, cs))
            for r in range(self._row, self._row + rs):
                for c in range(self._col, self._col + cs):
                    self._busy.add((r, c))
            self._col += cs


def _grid(html: str) -> list[tuple[int, int, int, int]]:
    p = _GridParser()
    p.feed(html or "")
    return p.cells


def _box(b) -> list[float]:
    x = [float(v) for v in b]
    return x[:4]


def _poly_box(p) -> list[float]:
    xs = [float(pt[0]) for pt in p]
    ys = [float(pt[1]) for pt in p]
    return [min(xs), min(ys), max(xs), max(ys)]


class PPStructureBackend:
    def __init__(self, lang: str = "korean", device: str = "cpu", cpu_threads: int | None = None,
                 **pipeline_kwargs) -> None:
        self.lang = lang
        self.device = "gpu:0" if device in ("gpu", "cuda") else device  # paddle device names
        self.pipeline_kwargs = pipeline_kwargs
        if cpu_threads:  # bound CPU inference threads (default: paddle picks, which can saturate the host)
            self.pipeline_kwargs["cpu_threads"] = int(cpu_threads)
        self._pipeline = None
        self._lock = threading.Lock()

    def _get_pipeline(self):
        if self._pipeline is None:
            with self._lock:
                if self._pipeline is None:
                    try:
                        from paddleocr import PPStructureV3
                    except ImportError as e:
                        raise ImportError(
                            "PPStructureBackend needs PaddleOCR>=3.2: "
                            "pip install paddlepaddle 'paddleocr[doc-parser]'"
                        ) from e
                    kw = dict(
                        lang=self.lang, device=self.device,
                        use_doc_orientation_classify=False, use_doc_unwarping=False,
                        use_textline_orientation=False, use_seal_recognition=False,
                        use_formula_recognition=False, use_chart_recognition=False,
                        use_region_detection=False, use_table_recognition=True,
                        enable_mkldnn=False,  # oneDNN path crashes on paddle 3.3 (PIR attr); CPU only anyway
                    )
                    kw.update(self.pipeline_kwargs)
                    self._pipeline = PPStructureV3(**kw)
        return self._pipeline

    def warmup(self) -> None:
        """Load model weights (downloads into the cache dir on first use unless pre-populated)."""
        self._get_pipeline()

    def analyze(self, page_image: bytes, words: list[Word] | None) -> PageStructure:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(page_image, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("page_image is not a decodable image")
        pipe = self._get_pipeline()
        with self._lock:  # paddle predictors are not thread-safe
            outs = list(pipe.predict(img))
        res = outs[0] if outs else {}

        regions: list[Region] = []
        tables: list[Table] = []
        table_boxes: list[tuple[list[float], float | None]] = []
        for b in (res.get("layout_det_res") or {}).get("boxes", []):
            rtype, level = _LABELS.get(str(b["label"]), ("paragraph", None))
            bbox, score = _box(b["coordinate"]), float(b.get("score", 0)) or None
            regions.append(Region(type=rtype, bbox=bbox, score=score, level=level))
            if rtype == "table":
                table_boxes.append((bbox, score))

        for i, t in enumerate(res.get("table_res_list") or []):
            cells_px = t.get("cell_box_list")
            cells_px = [] if cells_px is None else list(cells_px)  # paddle returns a numpy array
            html = t.get("pred_html") or ""
            grid = _grid(html)
            if i < len(table_boxes):
                tb = table_boxes[i][0]
            elif cells_px:
                tb = [min(c[0] for c in cells_px), min(c[1] for c in cells_px),
                      max(c[2] for c in cells_px), max(c[3] for c in cells_px)]
            else:
                continue
            # cell boxes are matched to <td> in order; if counts differ we pair up to the shorter
            cells = [
                TableCell(bbox=_box(cb), row=r, col=c, rowspan=rs, colspan=cs)
                for cb, (r, c, rs, cs) in zip(cells_px, grid)
            ]
            tables.append(Table(bbox=tb, cells=cells))

        ocr_words = None
        if words is None:
            o = res.get("overall_ocr_res") or {}
            texts = o.get("rec_texts") or []
            scores = o.get("rec_scores")
            scores = [] if scores is None else list(scores)
            boxes = o.get("rec_boxes")
            polys = o.get("rec_polys") if o.get("rec_polys") is not None else o.get("dt_polys")
            ocr_words = []
            for k, txt in enumerate(texts):
                if not str(txt).strip():
                    continue
                if boxes is not None and len(boxes) > k:
                    bb = _box(boxes[k])
                elif polys is not None and len(polys) > k:
                    bb = _poly_box(polys[k])
                else:
                    continue
                ocr_words.append(Word(text=str(txt), bbox=bb,
                                      conf=float(scores[k]) if k < len(scores) else None))
        return PageStructure(regions=regions, tables=tables, ocr_words=ocr_words,
                             meta={"backend": "ppstructure_v3", "lang": self.lang})
