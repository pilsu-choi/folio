"""HTML <table> parsing shared by VLM output and PP-StructureV3 (merged cells -> grid + spans)."""

from __future__ import annotations

from folio.parse.html_table import table_cells, to_grid
from folio.parse.text import md_blocks

MERGED = ("<table><tr><th rowspan=2>지역</th><th colspan=2>인구</th></tr>"
          "<tr><th>2025</th><th>2026</th></tr><tr><td>서울</td><td>9,3<br>86</td><td>9,401</td></tr></table>")


def test_rowspan_colspan_to_grid():
    (cells,) = table_cells(MERGED)
    grid, spans = to_grid(cells)
    assert grid == [["지역", "인구", ""], ["", "2025", "2026"], ["서울", "9,3 86", "9,401"]]
    assert spans == [{"row": 0, "col": 0, "rowspan": 2, "colspan": 1}, {"row": 0, "col": 1, "rowspan": 1, "colspan": 2}]


def test_span_past_last_row_is_clipped_and_bad_attrs_tolerated():
    (cells,) = table_cells('<table><tr><td rowspan="5">a</td><td colspan="x">b</td></tr><tr><td>c</td></tr></table>')
    grid, spans = to_grid(cells)
    assert grid == [["a", "b"], ["", "c"]] and spans == [{"row": 0, "col": 0, "rowspan": 2, "colspan": 1}]


def test_nested_table_text_stays_in_outer_cell():
    (cells,) = table_cells("<table><tr><td>x<table><tr><td>in</td></tr></table></td><td>y</td></tr></table>")
    assert [(c[1], c[4]) for c in cells] == [(0, "xin"), (1, "y")]


def test_md_blocks_html_tables_with_blank_lines_and_fences():
    md = ("# 제목\n\n앞 문단\n\n```html\n" + MERGED.replace("<tr><td>", "\n\n<tr><td>") + "\n```\n\n"
          "| p | q |\n| --- | --- |\n| 1 | 2 |\n\n뒤 문단\n")
    blocks = md_blocks(md, "vlm")
    assert [b["type"] for b in blocks] == ["heading", "paragraph", "table", "table", "paragraph"]
    html_tb, pipe_tb = blocks[2], blocks[3]
    assert html_tb["cells"][2] == ["서울", "9,3 86", "9,401"] and len(html_tb["spans"]) == 2
    assert pipe_tb["cells"] == [["p", "q"], ["1", "2"]] and "spans" not in pipe_tb
