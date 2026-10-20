"""PDF text-layer extraction (PyMuPDF) and per-page quality assessment (FR-02)."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

import pymupdf

Box = tuple[float, float, float, float]


@dataclass(slots=True)
class Atom:
    """Smallest matching unit: a word (default), a char (`char_match`) or an OCR word. Coordinates in pt."""

    text: str
    bbox: Box
    line: int
    block: int = 0
    size: float = 0.0
    conf: float | None = None

    @property
    def center(self) -> tuple[float, float]:
        return (self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2


def union(boxes) -> Box:
    boxes = list(boxes)
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)
    )


def extract_atoms(page: pymupdf.Page) -> tuple[list[Atom], list[Atom]]:
    """Return (chars, words) of the text layer in visible-page coordinates (after rotation / CropBox).

    Chars include whitespace chars so that char-level text equals PyMuPDF's text.
    """
    rm = page.rotation_matrix
    raw = page.get_text("rawdict")
    chars: list[Atom] = []
    words: list[Atom] = []
    line_id = 0
    for blk in raw["blocks"]:
        if blk.get("type") != 0:
            continue
        for line in blk["lines"]:
            line_id += 1
            cur: list[Atom] = []
            for span in line["spans"]:
                for ch in span["chars"]:
                    box = pymupdf.Rect(ch["bbox"]) * rm
                    a = Atom(ch["c"], (box.x0, box.y0, box.x1, box.y1), line_id, blk["number"], span["size"])
                    chars.append(a)
                    if ch["c"].isspace():
                        if cur:
                            words.append(_word(cur))
                            cur = []
                    else:
                        cur.append(a)
            if cur:
                words.append(_word(cur))
    return chars, words


def _word(chars: list[Atom]) -> Atom:
    c0 = chars[0]
    return Atom("".join(c.text for c in chars), union(c.bbox for c in chars), c0.line, c0.block, c0.size)


def _is_bad(ch: str) -> bool:
    if ch in "\t\n\r\f\v":
        return False
    return ch == "�" or unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Cn")


def assess_page(page: pymupdf.Page, words: list[Atom], *, min_chars: int = 10, max_bad_ratio: float = 0.2,
                image_ratio_min: float = 0.5, image_ratio_full: float = 0.8,
                min_text_area_ratio: float = 0.01) -> dict:
    """Judge the text layer. `ok=False` -> page should go through OCR."""
    text = "".join(w.text for w in words)
    n = len(text)
    bad = sum(_is_bad(c) for c in text)
    page_area = max(page.rect.width * page.rect.height, 1.0)
    img = 0.0
    try:
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"]) * page.rotation_matrix
            r.intersect(page.rect)
            img += max(r.width, 0) * max(r.height, 0)
    except Exception:
        pass
    img_ratio = min(img / page_area, 1.0)
    text_area = sum((w.bbox[2] - w.bbox[0]) * (w.bbox[3] - w.bbox[1]) for w in words) / page_area
    reason = None
    if n and bad / n > max_bad_ratio:
        reason = "garbled_text"
    elif n < min_chars and img_ratio >= image_ratio_min:
        reason = "no_text_over_image"
    elif img_ratio >= image_ratio_full and text_area < min_text_area_ratio:
        reason = "image_with_little_text"
    return {"ok": reason is None, "reason": reason, "chars": n, "bad_ratio": round(bad / n, 4) if n else 0.0,
            "image_ratio": round(img_ratio, 4), "text_area_ratio": round(text_area, 4)}


@dataclass(slots=True)
class PageInput:
    """One page ready for processing: visible size in pt, rendered PNG, scale px -> pt."""

    number: int
    width: float
    height: float
    png: bytes
    scale: float
    extras: dict = field(default_factory=dict)
