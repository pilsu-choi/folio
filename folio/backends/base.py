"""StructureBackend: "what is where on this page".

All coordinates here are in pixels of the `page_image` passed to `analyze` (top-left origin).
`folio.parse` converts them back to pt. Backends never build Document JSON.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

PixelBox = list[float]  # [x0, y0, x1, y1]

# Region types a backend may report; parse maps them onto Document JSON block types.
REGION_TYPES = (
    "heading", "paragraph", "list", "table", "kv", "figure", "caption", "header", "footer", "formula",
)


class Word(BaseModel):
    text: str
    bbox: PixelBox
    conf: float | None = None


class Region(BaseModel):
    type: str
    bbox: PixelBox
    score: float | None = None
    level: int | None = None
    key: str | None = None
    value: str | None = None


class TableCell(BaseModel):
    bbox: PixelBox
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    text: str | None = None  # backends that read text themselves may fill this; parse prefers words


class Table(BaseModel):
    bbox: PixelBox
    cells: list[TableCell] = Field(default_factory=list)


class PageStructure(BaseModel):
    regions: list[Region] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    ocr_words: list[Word] | None = None
    meta: dict = Field(default_factory=dict)


@runtime_checkable
class StructureBackend(Protocol):
    def analyze(self, page_image: bytes, words: list[Word] | None) -> PageStructure:
        """words given (digital page) → layout/table only; None → also OCR (fill `ocr_words`)."""
