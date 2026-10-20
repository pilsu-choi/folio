import pytest

from folio import chunk
from folio.contracts import Block, CellSpan, Document, Page, validate_chunk_record

ct = chunk.count_tokens


def B(type, text=None, sp=(), bbox=(0, 0, 10, 10), src="pdf_text", **kw):
    if bbox is None:
        src = "native"
    return Block(type=type, text=text, bbox=list(bbox) if bbox else None, text_source=src, section_path=list(sp), **kw)


def doc(*pages, numbers=None):
    nums = numbers or range(1, len(pages) + 1)
    return Document(
        doc_id="d1", source_uri="file:///x.pdf", content_hash="sha256:0", mime_type="application/pdf",
        pages=[Page(page=n, blocks=list(b)) for n, b in zip(nums, pages)],
    )


def words(n, w="word"):
    return " ".join(f"{w}{i}" for i in range(n)) + "."


def check(cs, mx):
    for c in cs:
        validate_chunk_record(c)
        assert ct(c["contextualized_content"]) <= mx, c["chunk_id"]


def test_small_section_one_chunk_with_heading_and_context():
    d = doc([B("heading", "Intro", [], level=1), B("paragraph", "Hello world.", ["Intro"]),
             B("heading", "Sub", ["Intro"], level=2), B("paragraph", "More text.", ["Intro", "Sub"])])
    cs = chunk.adaptive(d, min_tokens=0)
    assert len(cs) == 1
    c = cs[0]
    assert c["content"] == "Intro\n\nHello world.\n\nSub\n\nMore text."
    assert c["section_path"] == ["Intro"] and c["chunk_type"] == "section"
    assert c["contextualized_content"] == "Intro\n\n" + c["content"]
    assert c["chunk_strategy"] == "adaptive"


def test_descends_when_over_max_and_no_heading_only_chunks():
    d = doc([B("heading", "A", [], level=1), B("heading", "A1", ["A"], level=2), B("paragraph", words(60), ["A", "A1"]),
             B("heading", "A2", ["A"], level=2), B("paragraph", words(60), ["A", "A2"])])
    cs = chunk.adaptive(d, max_tokens=80, min_tokens=0)
    assert [c["section_path"] for c in cs] == [["A", "A1"], ["A", "A2"]]
    assert cs[0]["content"].startswith("A\n\nA1\n\nword0")  # parent heading rides on the first body chunk
    assert cs[1]["content"].startswith("A2\n\nword0")
    check(cs, 80)
    for c in cs:
        assert any(ch.startswith("word") for ch in c["content"].split("\n\n")[-1:])


def test_table_and_list_within_max_not_split():
    rows = [["h1", "h2"]] + [[f"r{i}", f"v{i}"] for i in range(10)]
    items = "\n".join(f"- item {i} text" for i in range(10))
    d = doc([B("heading", "S", [], level=1), B("paragraph", words(200), ["S"]), B("table", sp=["S"], cells=rows),
             B("list", items, ["S"])])
    cs = chunk.adaptive(d, max_tokens=250, min_tokens=0)
    check(cs, 250)
    assert any("| h1 | h2 |" in c["content"] and "| r9 | v9 |" in c["content"] for c in cs)
    assert any("- item 0 text" in c["content"] and "- item 9 text" in c["content"] for c in cs)


def test_big_table_row_groups_header_repeated_rowspan_together():
    rows = [["name", "value"]] + [[f"row{i}", f"val{i}"] for i in range(60)]
    spans = [CellSpan(row=10, col=0, rowspan=3)]
    d = doc([B("table", cells=rows, spans=spans)])
    cs = chunk.adaptive(d, max_tokens=60)
    assert len(cs) > 2
    check(cs, 60)
    for c in cs:
        assert c["content"].startswith("| name | value |\n| --- | --- |")
        assert c["chunk_type"] == "table"
    body = "\n".join(c["content"] for c in cs)
    assert all(f"| row{i} |" in body for i in range(60))
    holder = [c for c in cs if "| row10 |" in c["content"]][0]
    assert "| row11 |" in holder["content"] and "| row12 |" in holder["content"]
    assert len({c["chunk_id"] for c in cs}) == len(cs)


def test_list_subitems_stay_with_parent():
    lines = []
    for i in range(12):
        lines += [f"- parent {i} alpha beta gamma", f"  - child {i} one two", f"  - child {i} three four"]
    d = doc([B("list", "\n".join(lines))])
    cs = chunk.adaptive(d, max_tokens=60, min_tokens=0)
    assert len(cs) > 1
    check(cs, 60)
    for c in cs:
        ls = c["content"].split("\n")
        assert ls[0].startswith("- parent")
        for i, ln in enumerate(ls):
            if ln.startswith("  - child"):
                assert ls[i - 1].startswith(("- parent", "  - child"))
    assert "".join(c["content"] for c in cs).count("child") == 24


def test_code_split_prefers_blank_lines():
    blocks = ["\n".join(f"line_{b}_{i} = {i}" for i in range(5)) for b in range(6)]
    d = doc([B("code", "\n\n".join(blocks))])
    cs = chunk.adaptive(d, max_tokens=50, min_tokens=0)
    assert len(cs) > 1
    check(cs, 50)
    for c in cs:
        for seg in c["content"].split("\n\n"):
            assert seg in blocks  # never cut inside a blank-line separated segment


def test_code_without_blank_lines_splits_by_line():
    code = "\n".join(f"x{i} = {i}" for i in range(80))
    cs = chunk.adaptive(doc([B("code", code)]), max_tokens=40)
    check(cs, 40)
    assert "\n".join(c["content"] for c in cs) == code
    assert all(c["chunk_type"] == "code" for c in cs)


def test_paragraph_splits_on_sentences_and_oversized_sentence_falls_back_to_windows():
    para = " ".join(f"Sentence number {i} is here." for i in range(40))
    cs = chunk.adaptive(doc([B("paragraph", para)]), max_tokens=30)
    check(cs, 30)
    assert all(c["content"].endswith(".") for c in cs)
    long = " ".join(f"w{i}" for i in range(100))  # a single sentence over max
    cs = chunk.adaptive(doc([B("paragraph", long)]), max_tokens=20, overlap=3)
    check(cs, 20)
    assert len(cs) > 5
    assert set(cs[0]["content"].split()[-3:]) & set(cs[1]["content"].split()[:5])  # overlap applies to forced windows


def test_figure_kept_with_caption_and_never_split():
    d = doc([B("heading", "F", [], level=1), B("paragraph", words(100), ["F"]), B("figure", "A chart of revenue.", ["F"]),
             B("caption", "Figure 1. Revenue", ["F"])])
    cs = chunk.adaptive(d, max_tokens=110, min_tokens=0)
    check(cs, 110)
    fig = [c for c in cs if "chart" in c["content"]]
    assert len(fig) == 1 and "Figure 1. Revenue" in fig[0]["content"] and fig[0]["chunk_type"] == "figure"


def test_caption_before_table_attaches_to_it():
    d = doc([B("caption", "Table 1", ["S"]), B("table", sp=["S"], cells=[["a"], ["1"]]), B("paragraph", words(100), ["S"])])
    cs = chunk.adaptive(d, max_tokens=60, min_tokens=0)
    t = [c for c in cs if "| a |" in c["content"]][0]
    assert t["content"].startswith("Table 1") and t["chunk_type"] == "table"


def test_min_tokens_merges_siblings_but_not_across_parent_sections():
    d = doc([B("heading", "P1", [], level=1), B("heading", "a", ["P1"], level=2), B("paragraph", "tiny one.", ["P1", "a"]),
             B("heading", "b", ["P1"], level=2), B("paragraph", "tiny two.", ["P1", "b"]),
             B("heading", "P2", [], level=1), B("heading", "c", ["P2"], level=2), B("paragraph", "tiny three.", ["P2", "c"]),
             B("paragraph", words(100), ["P2", "c"])])
    cs = chunk.adaptive(d, max_tokens=60, min_tokens=20)
    # a+b merge under P1 (common path), c's chunks never merge with P1's
    assert cs[0]["section_path"] == ["P1"] and "tiny one." in cs[0]["content"] and "tiny two." in cs[0]["content"]
    assert all("tiny three" not in c["content"] for c in cs[:1])
    assert all(c["section_path"][:1] == ["P2"] for c in cs[1:])
    check(cs, 60)


def test_pages_field_for_section_spanning_pages_and_bbox():
    d = doc([B("heading", "S", [], bbox=(0, 0, 5, 5), level=1), B("paragraph", "p one.", ["S"], bbox=(2, 3, 9, 9))],
            [B("paragraph", "p two.", ["S"], bbox=(100, 100, 200, 200))])
    c = chunk.adaptive(d)[0]
    assert c["page"] == 1 and c["pages"] == [1, 2] and c["bbox"] == [0, 0, 9, 9]


def test_pptx_like_no_headings_no_page_size():
    slides = [[B("paragraph", words(10, f"s{n}_"), bbox=None), B("list", "- a\n- b", bbox=None)] for n in range(3)]
    d = doc(*slides)
    cs = chunk.adaptive(d, max_tokens=60, min_tokens=0)
    assert [c["page"] for c in cs] == [1, 2, 3]
    assert all(c["bbox"] is None and c["section_path"] == [] and c["chunk_type"] == "page" for c in cs)
    assert all(c["contextualized_content"] == c["content"] for c in cs)
    assert [c["page"] for c in chunk.page(d, max_tokens=60)] == [1, 2, 3]


def test_xlsx_like_sheets_with_section_path_no_bbox():
    d = doc([B("heading", "Sheet1", [], bbox=None, level=1), B("table", sp=["Sheet1"], bbox=None, cells=[["a", "b"], ["1", "2"]])],
            [B("heading", "Sheet2", [], bbox=None, level=1), B("table", sp=["Sheet2"], bbox=None, cells=[["c"], ["3"]])])
    cs = chunk.adaptive(d, min_tokens=0)
    assert [c["section_path"] for c in cs] == [["Sheet1"], ["Sheet2"]]
    assert [c["page"] for c in cs] == [1, 2] and all(c["bbox"] is None for c in cs)
    assert cs[0]["contextualized_content"].startswith("Sheet1\n\nSheet1\n\n| a | b |")


def test_markdown_like_single_page():
    d = doc([B("heading", "T", [], bbox=None, level=1), B("paragraph", words(30), ["T"], bbox=None),
             B("heading", "U", [], bbox=None, level=1), B("code", "print(1)", ["U"], bbox=None)])
    cs = chunk.adaptive(d, max_tokens=40, min_tokens=0)
    assert [c["section_path"] for c in cs] == [["T"], ["U"]] and all(c["pages"] == [1] for c in cs)


def test_header_footer_excluded_by_default():
    d = doc([B("header", "HDR"), B("paragraph", "body"), B("footer", "FTR")])
    assert chunk.adaptive(d)[0]["content"] == "body"
    assert "HDR" in chunk.adaptive(d, include_header_footer=True)[0]["content"]


def test_page_strategy_one_per_page_and_overflow_split():
    d = doc([B("paragraph", "alpha.")], [B("paragraph", "beta.")], [B("paragraph", words(30)), B("paragraph", words(30, "z"))])
    cs = chunk.page(d, max_tokens=50)
    assert [c["page"] for c in cs] == [1, 2, 3, 3]
    assert cs[0]["chunk_type"] == "page" and cs[0]["chunk_strategy"] == "page"
    check(cs, 50)


def test_fixed_strategy_windows_overlap():
    d = doc([B("heading", "H", [], level=1), B("paragraph", "This is a sentence about indexing. " * 60, ["H"])])
    cs = chunk.fixed(d, max_tokens=40, overlap=12)
    assert len(cs) > 3 and all(ct(c["content"]) <= 40 and c["chunk_type"] == "fixed" for c in cs)
    assert cs[1]["content"].split(".")[0] in cs[0]["content"]
    assert all(c["contextualized_content"] == c["content"] for c in cs)


def test_ids_deterministic_and_change_with_settings():
    d = doc([B("heading", "S", [], level=1), B("paragraph", words(150), ["S"])])
    a = chunk.adaptive(d, max_tokens=100)
    assert a == chunk.adaptive(d, max_tokens=100)
    assert {c["chunk_id"] for c in a}.isdisjoint(c["chunk_id"] for c in chunk.adaptive(d, max_tokens=101))
    assert len({c["chunk_id"] for c in a}) == len(a)
    assert chunk.adaptive(d)[0]["chunk_id"] != chunk.page(d)[0]["chunk_id"]


@pytest.mark.parametrize("text", ["word " * 500, "가나다라마바사아자차" * 100, "x" * 3000, "한국어 문장입니다. " * 80])
@pytest.mark.parametrize("strategy", ["adaptive", "page", "fixed"])
def test_max_tokens_guarantee(text, strategy):
    d = doc([B("heading", "H", [], level=1), B("paragraph", text, ["H"]), B("kv", sp=["H"], key="k", value=text)])
    cs = getattr(chunk, strategy)(d, max_tokens=25, overlap=5)
    assert cs
    for c in cs:
        assert ct(c["content"] if strategy == "fixed" else c["contextualized_content"]) <= 25


def test_custom_tokenizer():
    d = doc([B("paragraph", "a b c d e f g h i j")])
    cs = chunk.adaptive(d, max_tokens=5, min_tokens=0, tokenizer="builtins:len")  # counts characters
    assert len(cs) > 1 and all(len(c["content"]) <= 5 for c in cs)


def test_bad_options_fail_fast():
    from folio.errors import ConfigError

    d = doc([B("paragraph", "x")])
    with pytest.raises(ConfigError):
        chunk.adaptive(d, overlap_tokens=3)
    with pytest.raises(ConfigError):
        chunk.fixed(d, max_tokens=10, overlap=10)


def test_count_tokens_cjk_vs_latin():
    assert ct("hello world, 42") == 4
    assert ct("가나다") == 2
