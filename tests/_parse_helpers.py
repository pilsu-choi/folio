"""Shared helpers for parse/backends tests (no models)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pymupdf

from folio.backends.base import PageStructure, Region, Word

FIX = Path(__file__).parent / "fixtures"
SCHEMA = json.loads((Path(__file__).parents[1] / "folio/contracts/schemas/document.schema.json").read_text())


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def validate_doc(doc) -> None:
    jsonschema.validate(json.loads(doc.model_dump_json(exclude_none=True)), SCHEMA)


class FakeBackend:
    """One paragraph region per text line; OCR pages return preset words. Counts calls."""

    def __init__(self, ocr_words: list[Word] | None = None, fail_ocr_on: int | None = None):
        self.ocr_words = ocr_words
        self.calls = {"structure": 0, "ocr": 0}
        self.fail_ocr_on = fail_ocr_on

    def analyze(self, page_image: bytes, words: list[Word] | None) -> PageStructure:
        if words is None:
            self.calls["ocr"] += 1
            if self.fail_ocr_on == self.calls["ocr"]:
                raise RuntimeError("boom")
            ws = self.ocr_words or []
            return PageStructure(ocr_words=ws, regions=[Region(type="paragraph", bbox=w.bbox) for w in ws])
        self.calls["structure"] += 1
        lines: dict[int, list[Word]] = {}
        for w in sorted(words, key=lambda w: (round(w.bbox[1] / 4), w.bbox[0])):
            lines.setdefault(round(w.bbox[1] / 4), []).append(w)
        regs = []
        for ws in lines.values():
            regs.append(Region(type="paragraph", bbox=[min(w.bbox[0] for w in ws) - 1, min(w.bbox[1] for w in ws) - 1,
                                                       max(w.bbox[2] for w in ws) + 1, max(w.bbox[3] for w in ws) + 1]))
        return PageStructure(regions=regs)


def ocr_words_for(pdf_page_text_lines: list[str], px_per_line: int = 40) -> list[Word]:
    return [Word(text=t, bbox=[100, 100 + i * px_per_line, 100 + 20 * len(t), 130 + i * px_per_line], conf=0.95)
            for i, t in enumerate(pdf_page_text_lines)]


def page_text(path: Path, n: int) -> str:
    return pymupdf.open(path)[n].get_text()
