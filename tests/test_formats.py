"""Native formats (FR-27/28/29) and extra image types (FR-05). No models, no network."""

from __future__ import annotations

import json
import shutil

import pytest

from folio.parse import default
from folio.parse.convert import ConvertError
from tests._parse_helpers import FIX, FakeBackend, ocr_words_for, validate_doc

TRUTH = json.loads((FIX / "formats.truth.json").read_text())
MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(autouse=True)
def _no_vlm_env(monkeypatch):
    monkeypatch.delenv("FOLIO_VLM_URL", raising=False)


def blocks(doc, typ=None):
    return [(p, b) for p, b in doc.iter_blocks() if typ is None or b.type == typ]


def test_pptx_native():
    doc = default(str(FIX / "slides.pptx"))
    assert doc.mime_type == MIME_PPTX
    assert doc.meta["calls"] == {"structure": 0, "ocr": 0, "vlm": 0}
    assert doc.meta["vlm"] == {"enabled": False, "reason": "FOLIO_VLM_URL not set"}
    assert [p.page for p in doc.pages] == [1, 2, 3, 4]
    assert (doc.pages[0].width, doc.pages[0].height) == (720.0, 540.0)
    assert [b.text for _, b in blocks(doc, "heading")] == TRUTH["slides"]["titles"]
    assert all(b.level == 1 and b.text_source == "native" for _, b in blocks(doc, "heading"))
    # table cells + merge
    (_, tb), = blocks(doc, "table")
    assert tb.cells == TRUTH["slides"]["table"]
    assert [s.model_dump() for s in tb.spans] == TRUTH["slides"]["table_spans"]
    assert tb.section_path == ["재고 표"] and tb.bbox == [72.0, 144.0, 504.0, 273.6]
    # nested list in one block, section_path = slide title
    (_, li), = blocks(doc, "list")
    assert li.text.splitlines() == TRUTH["slides"]["bullets"] and li.section_path == ["주요 항목"]
    # figure: alt text, no VLM
    (pg, fig), = blocks(doc, "figure")
    assert pg.page == 4 and fig.text == TRUTH["slides"]["picture_alt"] and fig.text_source == "native"
    assert not any("노트" in b.section_path for _, b in doc.iter_blocks())  # notes off by default
    validate_doc(doc)


def test_pptx_notes_and_convert_mode():
    doc = default(str(FIX / "slides.pptx"), pptx_notes=True)
    notes = [b for _, b in blocks(doc) if b.section_path[-1:] == ["노트"]]
    assert [b.text for b in notes] == [TRUTH["slides"]["notes"]] and notes[0].section_path == ["그림 슬라이드", "노트"]
    validate_doc(doc)
    if not (shutil.which("soffice") or shutil.which("libreoffice")):
        with pytest.raises(ConvertError, match="LibreOffice"):
            default(str(FIX / "slides.pptx"), pptx_mode="convert", converter="local")


def test_xlsx_native():
    doc = default(str(FIX / "sheet.xlsx"))
    assert doc.mime_type == MIME_XLSX
    assert doc.meta["calls"] == {"structure": 0, "ocr": 0, "vlm": 0}
    assert [p.page for p in doc.pages] == [1, 2]  # hidden sheet excluded
    assert all(p.width is None and p.height is None for p in doc.pages)
    for page, (name, sheet) in zip(doc.pages, TRUTH["sheets"].items()):
        assert page.blocks[0].type == "heading" and page.blocks[0].text == name
        tables = [b for b in page.blocks if b.type == "table"]
        assert len(tables) == len(sheet["tables"])
        for b, t in zip(tables, sheet["tables"]):
            assert b.cells == t["cells"] and b.section_path == [name] and b.bbox is None
            assert [s.model_dump() for s in b.spans or []] == t.get("spans", [])
    assert doc.pages[0].blocks[2].cells[3][1:] == ["15", "1750.5"]  # formulas -> stored values
    validate_doc(doc)
    hidden = default(str(FIX / "sheet.xlsx"), xlsx_include_hidden=True)
    assert [p.blocks[0].text for p in hidden.pages] == ["매출", "일정", "비밀"]


def test_markdown_native():
    doc = default(str(FIX / "doc.md"))
    assert doc.mime_type == "text/markdown" and len(doc.pages) == 1 and doc.pages[0].width is None
    assert doc.meta["calls"] == {"structure": 0, "ocr": 0, "vlm": 0}
    heads = [(b.level, b.text, b.section_path) for _, b in blocks(doc, "heading")]
    assert [(lv, t) for lv, t, _ in heads] == [tuple(h) for h in TRUTH["md_headings"]]
    # section_path is exactly the ancestor heading chain
    chain: list[tuple[int, str]] = []
    for lv, t, sp in heads:
        chain = [c for c in chain if c[0] < lv]
        assert sp == [x for _, x in chain]
        chain.append((lv, t))
    lists = [b.text for _, b in blocks(doc, "list")]
    assert lists[0].startswith("1. ") and "  - 하위 항목 하나" in lists[1]
    (_, tb), = blocks(doc, "table")
    assert tb.cells == [["이름", "기본값"], ["dpi", "144"], ["vlm", "auto"]]
    (_, code), = blocks(doc, "code")
    assert code.text == 'print("hello")'
    (_, fig), = blocks(doc, "figure")
    assert fig.text == "아키텍처 그림"  # alt text only, link not followed
    assert all(b.text_source == "native" and b.bbox is None for _, b in doc.iter_blocks())
    validate_doc(doc)


def test_txt_encodings(tmp_path):
    doc = default(str(FIX / "legacy_cp949.txt"))
    assert doc.mime_type == "text/plain" and doc.meta["encoding"] == "cp949"
    got = [b.text for _, b in blocks(doc, "paragraph")]
    assert got == [p.strip() for p in TRUTH["txt"].split("\n\n")]
    assert doc.meta["calls"] == {"structure": 0, "ocr": 0, "vlm": 0}
    validate_doc(doc)
    for enc, name in (("utf-8-sig", "bom.txt"), ("euc-kr", "euc.txt"), ("utf-8", "u.txt")):
        f = tmp_path / name
        f.write_bytes(TRUTH["txt"].encode(enc))
        d = default(str(f))
        assert [b.text for _, b in d.iter_blocks()] == got, enc


@pytest.mark.parametrize("name,mime", [("image.bmp", "image/bmp"), ("image.webp", "image/webp")])
def test_bmp_webp(name, mime):
    fb = FakeBackend(ocr_words_for(["스캔 문서", "line two"]))
    doc = default(str(FIX / name), backend=fb)
    assert doc.mime_type == mime and len(doc.pages) == 1 and doc.meta["calls"]["ocr"] == 1
    assert all(b.text_source == "ocr" for _, b in doc.iter_blocks())
    validate_doc(doc)
