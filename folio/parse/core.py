"""`folio.parse.default`: file -> Document JSON (text layer + structure backend, VLM/OCR image path, native formats)."""

from __future__ import annotations

import io
import logging
import os
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from typing import Any

import pymupdf
from PIL import Image, ImageOps, ImageSequence

import folio
from folio.backends import resolve_backend
from folio.backends.base import PageStructure, Word
from folio.contracts import Block, Document, Page, content_hash, make_doc_id
from folio.errors import FolioError
from folio.parse import vlm as vlm_mod
from folio.parse.assemble import build_blocks, cluster_lines, finalize
from folio.parse.textlayer import Atom, assess_page, extract_atoms
from folio.parse.text import md_blocks, parse_text

log = logging.getLogger("folio.parse")

MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_HWPX = "application/hwp+zip"
_EXT_MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".tif": "image/tiff", ".tiff": "image/tiff", ".bmp": "image/bmp", ".webp": "image/webp",
             ".docx": MIME_DOCX, ".pptx": MIME_PPTX, ".xlsx": MIME_XLSX, ".hwpx": MIME_HWPX,
             ".hwp": "application/x-hwp", ".md": "text/markdown", ".markdown": "text/markdown",
             ".txt": "text/plain", ".text": "text/plain"}
_ZIP_MARKERS = {"ppt/presentation.xml": MIME_PPTX, "xl/workbook.xml": MIME_XLSX, "word/document.xml": MIME_DOCX}


class ParseError(FolioError):
    """The file cannot be read at all (corrupt / unsupported)."""


@dataclass
class Options:
    """See `default()` docstring."""

    backend: Any = "auto"
    ocr_fallback: bool = True
    dpi: int = 144
    char_match: bool = False
    vlm: Any = "auto"
    # text-layer quality thresholds (FR-02)
    min_chars: int = 10
    max_bad_char_ratio: float = 0.2
    image_ratio_min: float = 0.5
    image_ratio_full: float = 0.8
    min_text_area_ratio: float = 0.01
    # VLM on digital pages: embedded-image figures and low-quality structure tables (FR-06)
    figure_min_area: float = 0.01
    table_min_coverage: float = 0.8
    table_max_empty: float = 0.6
    # native formats
    pptx_mode: str = "native"
    pptx_notes: bool = False
    xlsx_include_hidden: bool = False


def read_bytes(uri: str) -> bytes:
    try:
        from folio.storage import read_uri
    except ImportError:
        read_uri = None
    if read_uri is not None:
        return read_uri(uri)
    path = uri.removeprefix("file://")
    with open(path, "rb") as f:
        return f.read()


def detect_mime(data: bytes, uri: str) -> str:
    ext = os.path.splitext(uri.split("?")[0])[1].lower()
    if data[:5] == b"%PDF-" or b"%PDF-" in data[:1024]:
        return "application/pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM" and data[6:10] == b"\x00\x00\x00\x00":
        return "image/bmp"
    if data[:4] == b"PK\x03\x04":
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
            for marker, mime in _ZIP_MARKERS.items():
                if marker in names:
                    return mime
        except zipfile.BadZipFile:
            pass
    if ext in _EXT_MIME:
        return _EXT_MIME[ext]
    raise ParseError(f"unsupported file type: {uri}")


def default(uri: str, **opts: Any) -> Document:
    """Parse `uri` into a Document. Dispatch by content/extension: PDF, images (PNG/JPG/TIFF/BMP/WebP),
    PPTX, XLSX, Markdown, TXT (DOCX/HWPX: NotImplementedError, FR-20/21).

    Opts: `backend` ("auto"|alias|"module:Class"|{fn, **kw}|object), `ocr_fallback=True`, `dpi=144`,
    `char_match=False`, text-layer thresholds (`min_chars`, `max_bad_char_ratio`, `image_ratio_min`,
    `image_ratio_full`, `min_text_area_ratio`).
    `vlm="auto"`: enabled iff FOLIO_VLM_URL is set (model FOLIO_VLM_MODEL); or {url, model, api_key, timeout}
    | "module:function" | callable(png[, task]) -> markdown/text; `False`/None/"off" disables. When enabled:
    image files and low-quality PDF pages -> VLM markdown (`text_source=vlm`); figures (PDF embedded images
    >= `figure_min_area` of the page, PPTX pictures, XLSX images) -> description; structure tables with
    `< table_min_coverage` word coverage, `> table_max_empty` empty cells or < 2 columns -> VLM table.
    A failed VLM request falls back to OCR (pages) or the structure result and is listed in
    `meta.vlm_fallbacks`. `meta.calls.vlm` counts requests; `meta.vlm` records enabled/reason.
    Native: `pptx_mode="native"|"convert"`, `pptx_notes=False`, `xlsx_include_hidden=False`.
    """
    o = Options(**opts)
    data = read_bytes(uri)
    mime = detect_mime(data, uri)
    if mime in (MIME_DOCX, MIME_HWPX, "application/x-hwp"):
        raise NotImplementedError("DOCX/HWPX parsing is deferred (FR-20/21)")
    t0 = time.perf_counter()
    caller, vinfo = vlm_mod.resolve(o.vlm)
    sess = vlm_mod.Session(caller)
    run = _Run(o, sess)
    backend_name = "native"
    if mime == "application/pdf" or mime.startswith("image/"):
        run.backend = resolve_backend(o.backend)
        backend_name = type(run.backend).__name__
    extra: dict = {}
    if mime == MIME_PPTX and o.pptx_mode == "convert":
        from folio.parse.pptx import convert_to_pdf

        data_pdf = convert_to_pdf(data)
        run.backend = resolve_backend(o.backend)
        backend_name = type(run.backend).__name__
        pages = run.pdf(data_pdf)
    elif mime == "application/pdf":
        pages = run.pdf(data)
    elif mime.startswith("image/"):
        pages = run.image(data)
    else:
        pages = run.native(data, mime, extra)
    if mime == "application/pdf" or mime.startswith("image/") or o.pptx_mode == "convert":
        finalize([[b for b in p["blocks"]] for p in pages])
    h = content_hash(data)
    meta = {
        "parser_version": f"folio-parse/{folio.__version__}",
        "backend": backend_name,
        "calls": {**run.calls, "vlm": sess.calls},
        "vlm": vinfo,
        "vlm_fallbacks": sess.fallbacks,
        "timings": {"pages_ms": [p["ms"] for p in pages], "total_ms": round((time.perf_counter() - t0) * 1000, 1)},
        "errors": run.errors,
        "page_quality": {p["page"]: p["quality"] for p in pages if p["quality"]},
        **extra,
    }
    doc_pages = [Page(page=p["page"], width=p["width"], height=p["height"],
                      blocks=[Block(**b) for b in p["blocks"]]) for p in pages]
    return Document(doc_id=make_doc_id(uri, h), source_uri=uri, content_hash=h, mime_type=mime,
                    pages=doc_pages, meta=meta)


def _table_is_weak(b: dict, words: list[Atom], o: Options) -> bool:
    """Structure-backend table result below threshold: degenerate grid, many empty cells, or words lost."""
    cells = b.get("cells") or []
    if not cells or len(cells[0]) < 2:
        return True
    covered = sum(s["rowspan"] * s["colspan"] - 1 for s in b.get("spans") or [])
    total = sum(len(r) for r in cells) - covered
    empty = sum(1 for r in cells for c in r if not c) - covered
    if total > 0 and empty / total > o.table_max_empty:
        return True
    x0, y0, x1, y1 = b["bbox"]
    inside = Counter(w.text for w in words if x0 <= w.center[0] <= x1 and y0 <= w.center[1] <= y1)
    got = Counter(" ".join(" ".join(r) for r in cells).split())
    n = sum(inside.values())
    return n > 0 and sum(min(c, got[t]) for t, c in inside.items()) / n < o.table_min_coverage


class _Run:
    def __init__(self, o: Options, sess):
        self.o, self.sess, self.backend = o, sess, None
        self.calls = {"structure": 0, "ocr": 0}
        self.errors: list[dict] = []

    # ---- native formats ----------------------------------------------------------------------
    def native(self, data: bytes, mime: str, extra: dict) -> list[dict]:
        t = time.perf_counter()
        try:
            if mime == MIME_PPTX:
                from folio.parse.pptx import parse_pptx

                pages = parse_pptx(data, self.o.pptx_notes, self.sess)
            elif mime == MIME_XLSX:
                from folio.parse.xlsx import parse_xlsx

                pages = parse_xlsx(data, self.o.xlsx_include_hidden, self.sess)
            else:
                pages, more = parse_text(data, mime)
                extra.update(more)
        except Exception as exc:
            raise ParseError(f"cannot read {mime}: {type(exc).__name__}: {exc}") from exc
        ms = round((time.perf_counter() - t) * 1000 / max(len(pages), 1), 1)
        for p in pages:
            p["ms"] = ms
        return pages

    # ---- inputs ------------------------------------------------------------------------------
    def pdf(self, data: bytes) -> list[dict]:
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
            if doc.page_count == 0:
                raise ValueError("no pages")
        except Exception as exc:
            raise ParseError(f"cannot open PDF: {exc}") from exc
        out = []
        for i in range(doc.page_count):
            out.append(self._page(i + 1, lambda i=i: self._pdf_page(doc, i)))
        return out

    def image(self, data: bytes) -> list[dict]:
        try:
            im = Image.open(io.BytesIO(data))
            frames = [ImageOps.exif_transpose(f.copy()) for f in ImageSequence.Iterator(im)]
        except Exception as exc:
            raise ParseError(f"cannot open image: {exc}") from exc
        out = []
        for i, fr in enumerate(frames):
            out.append(self._page(i + 1, lambda fr=fr, n=i + 1: self._image_page(n, fr)))
        return out

    def _page(self, n: int, fn) -> dict:
        t = time.perf_counter()
        res = {"page": n, "width": None, "height": None, "blocks": [], "quality": None}
        try:
            res.update(fn())
        except Exception as exc:
            log.warning("page %d failed: %s", n, exc)
            self.errors.append({"page": n, "error": f"{type(exc).__name__}: {exc}"})
        res["ms"] = round((time.perf_counter() - t) * 1000, 1)
        return res


    # ---- per page ----------------------------------------------------------------------------
    def _pdf_page(self, doc, i: int) -> dict:
        page = doc[i]
        n = i + 1
        w, h = page.rect.width, page.rect.height
        scale = 72 / self.o.dpi
        chars, words = extract_atoms(page)
        q = assess_page(page, words, min_chars=self.o.min_chars, max_bad_ratio=self.o.max_bad_char_ratio,
                        image_ratio_min=self.o.image_ratio_min, image_ratio_full=self.o.image_ratio_full,
                        min_text_area_ratio=self.o.min_text_area_ratio)
        png = page.get_pixmap(dpi=self.o.dpi).tobytes("png")
        if q["ok"] or not self.o.ocr_fallback:
            atoms = chars if self.o.char_match else words
            blocks = self._digital(n, png, words, atoms, scale)
            if self.sess.enabled:
                blocks = self._enrich(n, page, png, words, blocks, scale)
        else:
            blocks = self._image_path(n, png, w, h, scale)
        return {"width": round(w, 2), "height": round(h, 2), "blocks": blocks, "quality": q}

    def _image_page(self, n: int, fr: Image.Image) -> dict:
        dpi = fr.info.get("dpi") or (fr.info.get("resolution") if "resolution" in fr.info else None)
        x_dpi = float(dpi[0]) if isinstance(dpi, (tuple, list)) and dpi and dpi[0] > 1 else 72.0
        scale = 72.0 / x_dpi
        if fr.mode not in ("L", "RGB"):
            fr = fr.convert("RGB")
        buf = io.BytesIO()
        fr.save(buf, "PNG")
        png = buf.getvalue()
        w, h = fr.width * scale, fr.height * scale
        return {"width": round(w, 2), "height": round(h, 2), "blocks": self._image_path(n, png, w, h, scale),
                "quality": None}

    def _image_path(self, n: int, png: bytes, w: float, h: float, scale: float) -> list[dict]:
        """Image content page: VLM markdown when enabled (and it answers), else structure-backend OCR."""
        md = self.sess.ask("page", png, n)
        if md is not None:
            blocks = md_blocks(md, "vlm", [0.0, 0.0, w, h])
            if blocks:
                return blocks
            self.sess.note(n, "page", "no blocks in response")
        return self._ocr(n, png, scale)

    def _digital(self, n: int, png: bytes, words: list[Atom], atoms: list[Atom], scale: float) -> list[dict]:
        if not atoms:
            return []
        k = 1 / scale
        px = [Word(text=a.text, bbox=[round(c * k, 2) for c in a.bbox]) for a in words]
        try:
            struct = self.backend.analyze(png, px)
            self.calls["structure"] += 1
        except Exception as exc:  # degrade to text-layer paragraphs
            self.errors.append({"page": n, "error": f"structure backend failed: {type(exc).__name__}: {exc}"})
            struct = PageStructure()
        return build_blocks(atoms, struct, scale, "pdf_text", self.o.char_match)

    def _ocr(self, n: int, png: bytes, scale: float) -> list[dict]:
        struct = self.backend.analyze(png, None)
        self.calls["ocr"] += 1
        words = [Atom(w.text, (w.bbox[0] * scale, w.bbox[1] * scale, w.bbox[2] * scale, w.bbox[3] * scale),
                      0, 0, 0.0, w.conf) for w in (struct.ocr_words or []) if w.text.strip()]
        if not words:
            return []
        return build_blocks(cluster_lines(words), struct, scale, "ocr")

    def _enrich(self, n: int, page, png: bytes, words: list[Atom], blocks: list[dict], scale: float) -> list[dict]:
        """VLM on a digital page: describe figures (incl. embedded images the backend missed), redo weak tables."""
        im = Image.open(io.BytesIO(png))
        area = page.rect.width * page.rect.height

        def crop(box) -> bytes | None:
            x0, y0, x1, y1 = (v / scale for v in box)
            if x1 - x0 < 8 or y1 - y0 < 8:
                return None
            out = io.BytesIO()
            im.crop((max(int(x0), 0), max(int(y0), 0), min(int(x1) + 1, im.width), min(int(y1) + 1, im.height))
                    ).convert("RGB").save(out, "PNG")
            return out.getvalue()

        def overlap(a, b) -> float:
            iw = min(a[2], b[2]) - max(a[0], b[0])
            ih = min(a[3], b[3]) - max(a[1], b[1])
            return max(iw, 0) * max(ih, 0) / max((a[2] - a[0]) * (a[3] - a[1]), 1e-6)

        figs = [b for b in blocks if b["type"] == "figure"]
        try:
            for info in page.get_image_info():
                r = pymupdf.Rect(info["bbox"]) * page.rotation_matrix
                r.intersect(page.rect)
                box = [r.x0, r.y0, r.x1, r.y1]
                if r.is_empty or r.width * r.height < self.o.figure_min_area * area:
                    continue
                if any(overlap(box, f["bbox"]) > 0.5 or overlap(f["bbox"], box) > 0.5 for f in figs):
                    continue
                fig = {"type": "figure", "bbox": box, "text_source": "pdf_text"}
                figs.append(fig)
                blocks.append(fig)
        except Exception as exc:
            log.debug("image info failed: %s", exc)
        # a table region over an embedded image with no text in it is a false positive of the backend
        blocks[:] = [b for b in blocks if not (b["type"] == "table" and not any(c for r in b["cells"] for c in r)
                                               and any(overlap(b["bbox"], f["bbox"]) > 0.5 for f in figs))]
        for f in figs:
            desc = self.sess.ask("figure", crop(f["bbox"]), n)
            if desc:
                f["text"], f["text_source"] = desc.strip(), "vlm"
        for b in blocks:
            if b["type"] == "table" and b["text_source"] == "pdf_text" and _table_is_weak(b, words, self.o):
                md = self.sess.ask("table", crop(b["bbox"]), n)
                tb = next((x for x in md_blocks(md) if x["type"] == "table"), None) if md is not None else None
                if tb:
                    b.update(cells=tb["cells"], text=tb["text"], text_source="vlm")
                    b.pop("spans", None)
                elif md is not None:
                    self.sess.note(n, "table", "no table in response")
        blocks.sort(key=lambda b: (round(b["bbox"][1]), b["bbox"][0]))
        return blocks
