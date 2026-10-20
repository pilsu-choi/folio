"""Docling StructureBackend (optional dependency: `pip install docling`).

Layout + TableFormer via Docling's image pipeline; RapidOCR only when `words is None`.
"""

from __future__ import annotations

import os
import tempfile
import threading

from folio.backends.base import PageStructure, Region, Table, TableCell, Word

_LABELS = {
    "title": ("heading", 1), "section_header": ("heading", None), "text": ("paragraph", None),
    "paragraph": ("paragraph", None), "list_item": ("list", None), "table": ("table", None),
    "picture": ("figure", None), "chart": ("figure", None), "caption": ("caption", None),
    "page_header": ("header", None), "page_footer": ("footer", None),
    "key_value_region": ("kv", None), "formula": ("formula", None),
}


class DoclingBackend:
    def __init__(self, do_ocr_languages=("ko", "en"), table_mode: str = "accurate", device: str = "cpu",
                 cpu_threads: int | None = None, **kw):
        self.langs = list(do_ocr_languages)
        self.table_mode = table_mode
        self.device = "cuda" if device == "gpu" else device  # docling AcceleratorDevice names
        self.cpu_threads = int(cpu_threads) if cpu_threads else None
        self.kw = kw
        self._converters: dict[bool, object] = {}
        self._lock = threading.Lock()

    def warmup(self) -> None:
        """Build the converters (model weights load on first conversion)."""
        self._converter(False)
        self._converter(True)

    def _converter(self, ocr: bool):
        if ocr not in self._converters:
            try:
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import (
                    AcceleratorOptions,
                    PdfPipelineOptions,
                    TableFormerMode,
                )
                from docling.document_converter import DocumentConverter, ImageFormatOption
            except ImportError as e:  # pragma: no cover
                raise ImportError("DoclingBackend requires docling: pip install docling") from e
            o = PdfPipelineOptions(do_table_structure=True, do_ocr=ocr, generate_parsed_pages=True, **self.kw)
            o.accelerator_options = AcceleratorOptions(
                device=self.device, **({"num_threads": self.cpu_threads} if self.cpu_threads else {}))
            o.table_structure_options.mode = TableFormerMode(self.table_mode)
            o.table_structure_options.do_cell_matching = ocr  # no text cells without OCR: use model boxes
            o.layout_options.keep_empty_clusters = True  # keep layout items when no OCR text exists
            if ocr:
                from docling.datamodel.pipeline_options import RapidOcrOptions

                # RapidOCR serves one language per model; the Korean model also reads latin text.
                lang = {"ko": "korean", "en": "latin"}.get(self.langs[0], self.langs[0]) if self.langs else "ch"
                o.ocr_options = RapidOcrOptions(lang=[lang])
            self._converters[ocr] = DocumentConverter(
                format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=o)})
        return self._converters[ocr]

    @staticmethod
    def _ocr_cells(res):
        # prefer line-level cells (cleaner than the split word cells for mixed ko/latin text)
        if not res.pages:
            return []
        pp = getattr(res.pages[0], "parsed_page", None)
        lines = [c for c in (pp.textline_cells if pp else []) if c.from_ocr]
        return lines or res.pages[0].cells

    def analyze(self, page_image: bytes, words: list[Word] | None) -> PageStructure:
        import io

        from PIL import Image

        ocr = words is None
        img_w, img_h = Image.open(io.BytesIO(page_image)).size
        fd, path = tempfile.mkstemp(suffix=".png")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(page_image)
            with self._lock:
                res = self._converter(ocr).convert(path)
        finally:
            os.unlink(path)

        doc = res.document
        psize = doc.pages[min(doc.pages)].size if doc.pages else None
        pw, ph = (psize.width, psize.height) if psize else (img_w, img_h)
        sx, sy = img_w / pw, img_h / ph

        def box(b) -> list[float]:
            b = b.to_top_left_origin(ph)
            return [b.l * sx, b.t * sy, b.r * sx, b.b * sy]

        out = PageStructure(meta={"backend": "docling", "ocr": ocr})
        for item, _ in doc.iterate_items():
            label = getattr(item.label, "value", str(item.label))
            if label not in _LABELS or not item.prov:
                continue
            rtype, level = _LABELS[label]
            if rtype == "heading" and level is None:
                level = getattr(item, "level", None)
            region = Region(type=rtype, bbox=box(item.prov[0].bbox), level=level)
            out.regions.append(region)
            if rtype == "table" and hasattr(item, "data"):
                cells = [
                    TableCell(
                        bbox=box(c.bbox) if c.bbox else region.bbox,
                        row=c.start_row_offset_idx, col=c.start_col_offset_idx,
                        rowspan=max(1, c.end_row_offset_idx - c.start_row_offset_idx),
                        colspan=max(1, c.end_col_offset_idx - c.start_col_offset_idx),
                        text=c.text or None)
                    for c in item.data.table_cells if c.bbox
                ]
                out.tables.append(Table(bbox=region.bbox, cells=cells))

        if ocr:
            ow = []
            for c in self._ocr_cells(res):
                if not c.text.strip():
                    continue
                b = c.rect.to_bounding_box().to_top_left_origin(ph)
                ow.append(Word(text=c.text, bbox=[b.l * sx, b.t * sy, b.r * sx, b.b * sy], conf=c.confidence))
            out.ocr_words = ow
        return out
