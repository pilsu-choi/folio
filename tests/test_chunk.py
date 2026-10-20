import pytest

from folio import chunk
from folio.contracts import Block, Document, Page, validate_chunk_record


def B(type, text=None, sp=(), bbox=(0, 0, 10, 10), **kw):
    return Block(type=type, text=text, bbox=list(bbox), text_source="pdf_text", section_path=list(sp), **kw)


def doc(*pages):
    return Document(
        doc_id="d1", source_uri="file:///x.pdf", content_hash="sha256:0", mime_type="application/pdf",
        pages=[Page(page=i + 1, blocks=list(b)) for i, b in enumerate(pages)],
    )


SENT = "This is a sentence about indexing. "


def test_auto_picks_section_with_headings():
    d = doc([B("heading", "Intro", ["Intro"]), B("paragraph", "Hello world.", ["Intro"]),
             B("heading", "Methods", ["Methods"]), B("paragraph", "More text here.", ["Methods"])])
    cs = chunk.auto(d)
    assert [c["section_path"] for c in cs] == [["Intro"], ["Methods"]]
    assert all(c["chunk_strategy"] == "section" for c in cs)
    for c in cs:
        validate_chunk_record(c)


def test_auto_table_page_fixed():
    tbl = lambda: B("table", cells=[["a", "b"], ["1", "2"]])
    assert chunk.auto(doc([tbl(), tbl(), B("paragraph", "x")]))[0]["chunk_strategy"] == "table"
    assert chunk.auto(doc([B("paragraph", "one")], [B("paragraph", "two")]))[0]["chunk_strategy"] == "page"
    assert chunk.auto(doc([B("paragraph", "one"), B("paragraph", "two")]))[0]["chunk_strategy"] == "fixed"
    assert chunk.auto(doc([B("paragraph", "one")], [B("paragraph", "two")]), strategy="fixed")[0]["chunk_strategy"] == "fixed"


def test_page_chunks_and_pages_field():
    cs = chunk.page(doc([B("paragraph", "alpha")], [B("paragraph", "beta")]))
    assert [(c["page"], c["pages"], c["content"]) for c in cs] == [(1, [1], "alpha"), (2, [2], "beta")]


def test_header_footer_excluded_by_default():
    d = doc([B("header", "HDR"), B("paragraph", "body"), B("footer", "FTR")])
    assert chunk.fixed(d)[0]["content"] == "body"
    assert "HDR" in chunk.fixed(d, include_header_footer=True)[0]["content"]


def test_section_table_own_chunk_and_bbox_union():
    d = doc([B("heading", "H", ["H"], bbox=(0, 0, 5, 5)), B("paragraph", "p", ["H"], bbox=(2, 3, 9, 9)),
             B("table", sp=["H"], cells=[["h"], ["v"]]), B("paragraph", "q", ["H"])])
    cs = chunk.section(d)
    assert [c["chunk_type"] for c in cs] == ["section", "table", "section"]
    assert cs[0]["bbox"] == [0, 0, 9, 9]


def test_table_split_repeats_header():
    rows = [["name", "value"]] + [[f"row{i}", f"val{i}"] for i in range(40)]
    d = doc([B("table", cells=rows)])
    cs = chunk.table(d, max_tokens=30)
    assert len(cs) > 1
    for c in cs:
        assert c["content"].startswith("name | value")
        assert chunk.count_tokens(c["content"]) <= 30
    body = "\n".join(c["content"] for c in cs)
    for i in range(40):
        assert f"row{i} |" in body


def test_fixed_overlap_and_max_tokens():
    d = doc([B("paragraph", SENT * 60)])
    cs = chunk.fixed(d, max_tokens=40, overlap_tokens=12)
    assert len(cs) > 3
    assert all(chunk.count_tokens(c["content"]) <= 40 for c in cs)
    assert cs[1]["content"].split(".")[0] in cs[0]["content"]  # overlapping sentence repeated


@pytest.mark.parametrize("text", ["word " * 500, "가나다라마바사아자차" * 100, "x" * 3000, SENT * 50, "한국어 문장입니다. " * 80])
@pytest.mark.parametrize("strategy", ["section", "table", "page", "fixed"])
def test_max_tokens_guarantee(text, strategy):
    d = doc([B("heading", "H", ["H"]), B("paragraph", text, ["H"])])
    cs = getattr(chunk, strategy)(d, max_tokens=25, overlap_tokens=5)
    assert cs and all(chunk.count_tokens(c["content"]) <= 25 for c in cs)


def test_min_tokens_merges_small():
    d = doc([B("heading", "A", ["A"]), B("paragraph", "tiny", ["A"]), B("heading", "B", ["B"]), B("paragraph", "small", ["B"])])
    assert len(chunk.section(d)) == 2
    assert len(chunk.section(d, min_tokens=10)) == 1


def test_custom_tokenizer_and_determinism():
    d = doc([B("paragraph", "a b c d e f g h i j")])
    a = chunk.fixed(d, max_tokens=3, tokenizer="builtins:len")  # counts characters
    assert all(len(c["content"]) <= 3 for c in a)
    assert chunk.fixed(d, max_tokens=4) == chunk.fixed(d, max_tokens=4)
    assert [c["chunk_id"] for c in chunk.fixed(d)] == [c["chunk_id"] for c in chunk.fixed(d)]


def test_count_tokens_cjk_vs_latin():
    assert chunk.count_tokens("hello world, 42") == 4
    assert chunk.count_tokens("가나다") == 2
