from __future__ import annotations

import json

import pymupdf
import pytest

from folio.backends.heuristic import HeuristicBackend
from folio.parse import ParseError, default
from tests._parse_helpers import FIX, FakeBackend, norm, ocr_words_for, validate_doc


@pytest.fixture(autouse=True)
def _no_vlm_env(monkeypatch):
    monkeypatch.delenv("FOLIO_VLM_URL", raising=False)


def words_of(text: str) -> list[str]:
    return norm(text).split(" ") if norm(text) else []


def test_digital_no_ocr_text_matches_pymupdf():
    fb = FakeBackend()
    doc = default(str(FIX / "digital.pdf"), backend=fb)
    assert doc.meta["calls"] == {"structure": 2, "ocr": 0, "vlm": 0}
    assert fb.calls["ocr"] == 0
    src = pymupdf.open(FIX / "digital.pdf")
    for page, pg in zip(doc.pages, src):
        assert all(b.text_source == "pdf_text" for b in page.blocks)
        got = " ".join(norm(b.text) for b in page.blocks)
        assert sorted(words_of(got)) == sorted(words_of(pg.get_text()))
        for b in page.blocks:  # exact per-block check against clipped PyMuPDF text
            assert norm(b.text) == norm(pg.get_text("text", clip=pymupdf.Rect(b.bbox)))
    validate_doc(doc)
    assert doc.meta["parser_version"] and doc.meta["timings"]["total_ms"] >= 0 and doc.meta["errors"] == []


def test_default_heuristic_headings_and_section_path():
    doc = default(str(FIX / "contract_ko.pdf"), backend="heuristic")
    heads = [b for _, b in doc.iter_blocks() if b.type == "heading"]
    assert [h.text for h in heads][:3] == ["용역 계약서", "제1조 (목적)", "제2조 (계약기간)"]
    assert heads[0].level == 1 and heads[1].level == 2
    para = [b for _, b in doc.iter_blocks() if b.type == "paragraph" and "소프트웨어" in b.text][0]
    assert para.section_path == ["용역 계약서", "제1조 (목적)"]
    validate_doc(doc)


def test_table_cells_match_truth():
    truth = json.loads((FIX / "table.truth.json").read_text())
    doc = default(str(FIX / "table.pdf"), backend=HeuristicBackend())
    tb = [b for _, b in doc.iter_blocks() if b.type == "table"]
    assert len(tb) == 1
    assert len(tb[0].cells) == truth["rows"] and all(len(r) == truth["cols"] for r in tb[0].cells)
    assert tb[0].cells == truth["cells"]
    assert [s.model_dump() for s in tb[0].spans] == truth["spans"]
    validate_doc(doc)


def test_scanned_and_mixed_ocr_only_where_needed():
    fb = FakeBackend(ocr_words_for(["스캔 문서", "OCR text"]))
    doc = default(str(FIX / "mixed.pdf"), backend=fb)
    src = [{b.text_source for b in p.blocks} for p in doc.pages]
    assert src == [{"pdf_text"}, {"ocr"}, {"ocr"}]
    assert doc.meta["calls"]["ocr"] == 2 == fb.calls["ocr"]
    assert doc.meta["page_quality"][3]["reason"] == "garbled_text"
    validate_doc(doc)


def test_ocr_fallback_disabled():
    fb = FakeBackend(ocr_words_for(["x"]))
    doc = default(str(FIX / "scanned.pdf"), backend=fb, ocr_fallback=False)
    assert fb.calls["ocr"] == 0 and doc.meta["calls"]["ocr"] == 0


def test_rotation_and_cropbox_bboxes_visible():
    doc = default(str(FIX / "rotated.pdf"), backend=FakeBackend())
    src = pymupdf.open(FIX / "rotated.pdf")
    assert [(p.width, p.height) for p in doc.pages] == [(842.0, 595.0), (460.0, 550.0)]
    for page, pg in zip(doc.pages, src):
        assert page.blocks
        pix = pg.get_pixmap(dpi=72, colorspace=pymupdf.csGRAY)
        for b in page.blocks:
            x0, y0, x1, y1 = (round(v) for v in b.bbox)
            assert 0 <= x0 < x1 <= pix.width + 20 and 0 <= y0 < y1 <= pix.height
            samples = pix.samples
            dark = any(samples[y * pix.stride + x] < 128 for y in range(y0, min(y1, pix.height))
                       for x in range(x0, min(x1, pix.width)))
            assert dark, b.text
    assert "Conclusion" in " ".join(b.text for b in doc.pages[0].blocks)
    validate_doc(doc)


def test_char_match_text_complete():
    doc = default(str(FIX / "digital.pdf"), backend=FakeBackend(), char_match=True)
    src = pymupdf.open(FIX / "digital.pdf")
    for page, pg in zip(doc.pages, src):
        got = "".join(norm(b.text).replace(" ", "") for b in page.blocks)
        assert sorted(got) == sorted(norm(pg.get_text()).replace(" ", ""))
    validate_doc(doc)


@pytest.mark.parametrize("name,pages", [("image.png", 1), ("image.jpg", 1), ("multi.tiff", 2)])
def test_image_inputs(name, pages):
    fb = FakeBackend(ocr_words_for(["스캔 문서", "line two"]))
    doc = default(str(FIX / name), backend=fb)
    assert len(doc.pages) == pages and doc.meta["calls"]["ocr"] == pages
    assert all(b.text_source == "ocr" for _, b in doc.iter_blocks())
    if name.startswith("image."):  # 100 dpi image of a 595x842pt page
        assert doc.pages[0].width == pytest.approx(595, abs=2)
    validate_doc(doc)


def test_page_failure_isolated():
    fb = FakeBackend(ocr_words_for(["x y z"]), fail_ocr_on=1)
    doc = default(str(FIX / "mixed.pdf"), backend=fb)
    assert [e["page"] for e in doc.meta["errors"]] == [2]
    assert len(doc.pages) == 3 and doc.pages[0].blocks and doc.pages[2].blocks and not doc.pages[1].blocks
    validate_doc(doc)


def test_backend_failure_on_digital_page_degrades_to_text_layer():
    class Boom:
        def analyze(self, img, words):
            raise RuntimeError("down")

    doc = default(str(FIX / "digital.pdf"), backend=Boom())
    assert len(doc.meta["errors"]) == 2
    assert "Overview" in " ".join(b.text for b in doc.pages[0].blocks)


def test_unreadable_and_unsupported(tmp_path):
    with pytest.raises(ParseError):
        default(str(FIX / "corrupt.pdf"), backend=FakeBackend())
    u = tmp_path / "a.bin"
    u.write_bytes(b"\x00\x01 unknown")
    with pytest.raises(ParseError, match="unsupported"):
        default(str(u))


def test_ids_deterministic_and_file_uri():
    a = default(str(FIX / "digital.pdf"), backend=FakeBackend())
    b = default(str(FIX / "digital.pdf"), backend=FakeBackend())
    assert a.doc_id == b.doc_id and a.content_hash.startswith("sha256:")
    c = default("file://" + str(FIX / "digital.pdf"), backend=FakeBackend())
    assert c.doc_id != a.doc_id and c.content_hash == a.content_hash


def test_resolve_backend_specs(monkeypatch):
    from folio.backends import resolve_backend
    from folio.backends.http import HttpBackend

    monkeypatch.delenv("FOLIO_STRUCTURE_URL", raising=False)
    assert isinstance(resolve_backend("auto"), HeuristicBackend)
    monkeypatch.setenv("FOLIO_STRUCTURE_URL", "http://x:9000")
    assert isinstance(resolve_backend("auto"), HttpBackend)
    b = resolve_backend({"fn": "folio.backends.heuristic:HeuristicBackend", "heading_ratio": 1.5})
    assert b.heading_ratio == 1.5
    obj = FakeBackend()
    assert resolve_backend(obj) is obj


@pytest.mark.parametrize("rotation", [0, 90, 270])
def test_words_split_on_gaps_without_space_glyphs(rotation):
    """LibreOffice/rhwp/Hancom PDFs may draw Korean words without space glyphs, or draw the spaces out of order."""
    from folio.parse.textlayer import extract_atoms

    doc = pymupdf.open()
    page = doc.new_page()
    page.set_rotation(rotation)  # text then runs vertically on the visible page
    tw, font = pymupdf.TextWriter(page.rect), pymupdf.Font("korea")
    for x, ch in zip([72, 84, 99, 111, 123], "본계약은부"):  # a space-wide gap after 계, no space glyph
        tw.append((x, 100), ch, font=font, fontsize=12)
    tw.write_text(page)
    tw = pymupdf.TextWriter(page.rect)
    tw.append((96, 100), " ", font=font, fontsize=12)  # the space drawn later, inside the gap
    tw.append((135, 100), "품", font=font, fontsize=12)  # continues 부 in a later text run
    for x, ch in zip([72, 88, 104, 120, 144], "경상북도법"):  # letter-spaced (distributed) text, then a word gap
        tw.append((x, 140), ch, font=font, fontsize=12)
    tw.write_text(page)
    page = doc.reload_page(page)
    assert [a.text for a in extract_atoms(page)[1]] == ["본계", "약은부품", "경상북도", "법"]


def test_article_paragraphs_become_sections():
    from folio.parse.assemble import apply_sections

    blocks = [{"type": "heading", "text": "예금거래기본약관", "level": 1},
              {"type": "paragraph", "text": "제1조(적용범위) 이 약관은 예금거래에 적용한다."},
              {"type": "paragraph", "text": "제2조(실명거래)"},
              {"type": "paragraph", "text": "거래처는 실명으로 거래한다."},
              {"type": "paragraph", "text": "제3조에 따라 처리한다."},
              {"type": "heading", "text": "부칙", "level": 1},
              {"type": "paragraph", "text": "이 약관은 공포일부터 시행한다."}]
    apply_sections(blocks)
    assert [b["section_path"] for b in blocks] == [
        [], ["예금거래기본약관", "제1조(적용범위)"], ["예금거래기본약관"], ["예금거래기본약관", "제2조(실명거래)"],
        ["예금거래기본약관", "제2조(실명거래)"], [], ["부칙"]]
    assert blocks[1]["type"] == "paragraph" and (blocks[2]["type"], blocks[2]["level"]) == ("heading", 2)
