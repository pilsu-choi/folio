"""Document JSON: the parse stage output contract."""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

BBox = list[float]  # [x0, y0, x1, y1] in pt, top-left origin


class BlockType(str, Enum):
    heading = "heading"
    paragraph = "paragraph"
    list = "list"
    table = "table"
    kv = "kv"
    figure = "figure"
    caption = "caption"
    code = "code"
    header = "header"
    footer = "footer"


class TextSource(str, Enum):
    pdf_text = "pdf_text"
    ocr = "ocr"
    native = "native"
    vlm = "vlm"


class CellSpan(BaseModel):
    """A merged cell: the cell at (row, col) covers rowspan x colspan grid cells."""

    model_config = ConfigDict(extra="forbid")

    row: int = Field(ge=0)
    col: int = Field(ge=0)
    rowspan: int = Field(default=1, ge=1)
    colspan: int = Field(default=1, ge=1)


class Block(BaseModel):
    model_config = ConfigDict(extra="allow", use_enum_values=True)

    type: BlockType
    text: str | None = None
    bbox: BBox | None = Field(default=None, min_length=4, max_length=4)
    text_source: TextSource
    section_path: list[str] = Field(default_factory=list)
    cells: list[list[str]] | None = None
    spans: list[CellSpan] | None = None
    key: str | None = None
    value: str | None = None
    level: int | None = Field(default=None, ge=1, description="heading level, 1 = top")

    @model_validator(mode="after")
    def _type_specific(self) -> Block:
        if self.type == BlockType.table.value:
            if self.cells is None:
                raise ValueError("table block requires `cells`")
        elif self.type == BlockType.kv.value:
            if self.key is None or self.value is None:
                raise ValueError("kv block requires `key` and `value`")
        elif self.type != BlockType.figure.value and self.text is None:
            raise ValueError(f"{self.type} block requires `text`")
        if self.text_source != TextSource.native.value and self.bbox is None:
            raise ValueError("`bbox` is required unless text_source is native")
        return self


class Page(BaseModel):
    model_config = ConfigDict(extra="allow")

    page: int | None = Field(
        default=None, ge=1,
        description="1-based: PDF/image page, PPTX slide, XLSX sheet order, 1 for Markdown/TXT",
    )
    width: float | None = Field(default=None, description="pt; None for XLSX/Markdown/TXT/native DOCX·HWPX")
    height: float | None = None
    blocks: list[Block] = Field(default_factory=list)


class Document(BaseModel):
    model_config = ConfigDict(extra="allow")

    doc_id: str
    source_uri: str
    content_hash: str
    mime_type: str
    pages: list[Page] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    # PDF that `convert` mode parsed (pages/bbox refer to it); not serialized, the pipeline stores it as converted.pdf
    _converted_pdf: bytes | None = PrivateAttr(default=None)

    def iter_blocks(self):
        for page in self.pages:
            for block in page.blocks:
                yield page, block


def content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def make_doc_id(source_uri: str, content_hash: str) -> str:
    """Deterministic id from `source_uri + content_hash`."""
    return hashlib.sha256(f"{source_uri}\n{content_hash}".encode()).hexdigest()[:32]
