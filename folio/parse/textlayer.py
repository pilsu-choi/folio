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
    Words split on whitespace chars and on visible gaps: some producers (LibreOffice with a fallback CJK font,
    rhwp, Hancom, OnlyOffice) draw Korean text without space glyphs or with the spaces drawn elsewhere, which
    also breaks PyMuPDF lines inside a word. A gap counts when it exceeds the line's usual letter gap by a
    space width, so letter-spaced (distributed) text stays one word. A line that starts right where the previous
    word ended continues that word.
    """
    rm = page.rotation_matrix
    turn = pymupdf.Matrix(rm.a, rm.b, rm.c, rm.d, 0, 0)
    raw = page.get_text("rawdict")
    chars: list[Atom] = []
    words: list[Atom] = []
    tail: list[Atom] = []  # chars of the last word if it ended at a line end, not at a space or gap
    tail_dir = None
    line_id = 0
    for blk in raw["blocks"]:
        if blk.get("type") != 0:
            continue
        for line in blk["lines"]:
            line_id += 1
            atoms = []
            for span in line["spans"]:
                for ch in span["chars"]:
                    box = pymupdf.Rect(ch["bbox"]) * rm
                    atoms.append(Atom(ch["c"], (box.x0, box.y0, box.x1, box.y1), line_id, blk["number"], span["size"]))
            chars.extend(atoms)
            v = pymupdf.Point(line["dir"]) * turn
            d = _DIRS.get((round(v.x), round(v.y))) if abs(v.x) < 0.01 or abs(v.y) < 0.01 else None
            gaps = sorted(max(0.0, _gap(a, b, d)) for a, b in zip(atoms, atoms[1:])
                          if d and not (a.text.isspace() or b.text.isspace()))
            base = gaps[(len(gaps) - 1) // 2] if gaps else 0.0
            if d != tail_dir:
                tail = []
            cur: list[Atom] = []
            for a in atoms:
                if a.text.isspace():
                    if tail and _adjacent(tail[-1], a, d):  # a space drawn out of order elsewhere keeps it open
                        tail = []
                    if cur:
                        words.append(_word(cur))
                        cur = []
                    continue
                if not cur and tail and _adjacent(tail[-1], a, d):
                    words.pop()
                    cur = tail
                elif cur and d and not _adjacent(cur[-1], a, d, base):
                    words.append(_word(cur))
                    cur = []
                tail = []
                cur.append(a)
            if cur:
                words.append(_word(cur))
                tail, tail_dir = (cur, d) if d else ([], None)
    return chars, words


# writing direction on the visible page -> (start edge, end edge, baseline edge) indexes into a bbox; sign
_DIRS = {(1, 0): (0, 2, 3, 1), (-1, 0): (2, 0, 1, -1), (0, 1): (1, 3, 0, 1), (0, -1): (3, 1, 2, -1)}


def _gap(a: Atom, b: Atom, d) -> float:
    start, end, _, sign = d
    return (b.bbox[start] - a.bbox[end]) * sign


def _adjacent(a: Atom, b: Atom, d, base: float = 0.0) -> bool:
    """`b` follows `a` on the same baseline without a word gap (a space is ~0.25-0.33 em beyond `base`)."""
    if not d:
        return False
    size = max(a.size, b.size, 1.0)
    # glyph boxes of condensed fonts overlap; a large step back is a new line, not the next letter
    return -0.5 * size <= _gap(a, b, d) <= base + 0.24 * size and abs(a.bbox[d[2]] - b.bbox[d[2]]) <= 0.3 * size


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
