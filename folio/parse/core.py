"""`folio.parse.default`: file -> Document JSON (text layer + structure backend, OCR/VLM fallbacks)."""

from __future__ import annotations

import io
import logging
import os
import time
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

log = logging.getLogger("folio.parse")

MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_HWPX = "application/hwp+zip"
_EXT_MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".tif": "image/tiff", ".tiff": "image/tiff", ".docx": MIME_DOCX, ".hwpx": MIME_HWPX,
             ".hwp": "application/x-hwp"}


class ParseError(FolioError):
    """The file cannot be read at all (corrupt / unsupported)."""


@dataclass
class Options:
    backend: Any = "auto"
    ocr_fallback: bool = True
    dpi: int = 144
    char_match: bool = False
    vlm: Any = None
    vlm_pages: Any = None  # None | "all" | list[int]
    # text-layer quality thresholds (FR-02)
    min_chars: int = 10
    max_bad_char_ratio: float = 0.2
    image_ratio_min: float = 0.5
    image_ratio_full: float = 0.8
    min_text_area_ratio: float = 0.01
    # VLM trigger thresholds on the OCR result
    vlm_min_words: int = 3
    vlm_min_conf: float = 0.5


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
    if ext in _EXT_MIME:
        return _EXT_MIME[ext]
    raise ParseError(f"unsupported file type: {uri}")


def default(uri: str, **opts: Any) -> Document:
    o = Options(**opts)
    data = read_bytes(uri)
    mime = detect_mime(data, uri)
    if mime in (MIME_DOCX, MIME_HWPX, "application/x-hwp"):
        raise NotImplementedError("DOCX/HWPX parsing is deferred (FR-20/21)")
    t0 = time.perf_counter()
    backend = resolve_backend(o.backend)
    vlm_call = vlm_mod.make_caller(o.vlm) if o.vlm else None
    run = _Run(o, backend, vlm_call)
    pages = run.pdf(data) if mime == "application/pdf" else run.image(data)
    finalize([[b for b in p["blocks"]] for p in pages])
    h = content_hash(data)
    meta = {
        "parser_version": f"folio-parse/{folio.__version__}",
        "backend": type(backend).__name__,
        "calls": run.calls,
        "timings": {"pages_ms": [p["ms"] for p in pages], "total_ms": round((time.perf_counter() - t0) * 1000, 1)},
        "errors": run.errors,
        "page_quality": {p["page"]: p["quality"] for p in pages if p["quality"]},
    }
    doc_pages = [Page(page=p["page"], width=p["width"], height=p["height"],
                      blocks=[Block(**b) for b in p["blocks"]]) for p in pages]
    return Document(doc_id=make_doc_id(uri, h), source_uri=uri, content_hash=h, mime_type=mime,
                    pages=doc_pages, meta=meta)


class _Run:
    def __init__(self, o: Options, backend, vlm_call):
        self.o, self.backend, self.vlm_call = o, backend, vlm_call
        self.calls = {"structure": 0, "ocr": 0, "vlm": 0}
        self.errors: list[dict] = []

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
        w, h = page.rect.width, page.rect.height
        scale = 72 / self.o.dpi
        chars, words = extract_atoms(page)
        q = assess_page(page, words, min_chars=self.o.min_chars, max_bad_ratio=self.o.max_bad_char_ratio,
                        image_ratio_min=self.o.image_ratio_min, image_ratio_full=self.o.image_ratio_full,
                        min_text_area_ratio=self.o.min_text_area_ratio)
        png = page.get_pixmap(dpi=self.o.dpi).tobytes("png")
        if q["ok"] or not self.o.ocr_fallback:
            atoms = chars if self.o.char_match else words
            blocks = self._digital(i + 1, png, words, atoms, scale)
            source = "pdf_text"
        else:
            blocks = self._ocr(i + 1, png, scale)
            source = "ocr"
        blocks = self._maybe_vlm(i + 1, png, w, h, blocks, source)
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
        blocks = self._maybe_vlm(n, png, w, h, self._ocr(n, png, scale), "ocr")
        return {"width": round(w, 2), "height": round(h, 2), "blocks": blocks, "quality": None}

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
        self._last_ocr = words
        if not words:
            return []
        return build_blocks(cluster_lines(words), struct, scale, "ocr")

    def _maybe_vlm(self, n: int, png: bytes, w: float, h: float, blocks: list[dict], source: str) -> list[dict]:
        if self.vlm_call is None:
            return blocks
        vp = self.o.vlm_pages
        if vp == "all" or (isinstance(vp, (list, tuple, set)) and n in vp):
            trigger = True
        elif source == "ocr":
            confs = [a.conf for a in getattr(self, "_last_ocr", []) if a.conf is not None]
            trigger = len(getattr(self, "_last_ocr", [])) < self.o.vlm_min_words or \
                (bool(confs) and sum(confs) / len(confs) < self.o.vlm_min_conf)
        else:
            trigger = False
        if not trigger:
            return blocks
        try:
            md = self.vlm_call(png)
            self.calls["vlm"] += 1
            new = vlm_mod.markdown_to_blocks(md, w, h)
            return new or blocks
        except Exception as exc:
            self.errors.append({"page": n, "error": f"vlm failed: {type(exc).__name__}: {exc}"})
            return blocks
