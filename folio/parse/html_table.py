"""HTML <table> -> grid cells with rowspan/colspan (VLM table output, PP-StructureV3 `pred_html`)."""

from __future__ import annotations

import re
from html.parser import HTMLParser

Cell = tuple[int, int, int, int, str]  # row, col, rowspan, colspan, text


class _TableParser(HTMLParser):
    """Cells per top-level <table> in document order, placed on the grid honoring spans. A nested table's text
    goes into the enclosing cell."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[Cell]] = []
        self._depth = 0
        self._cell: list | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
                self._row, self._col, self._busy = -1, 0, set()
            return
        if self._depth != 1:
            return
        if tag == "tr":
            self._row += 1
            self._col = 0
        elif tag in ("td", "th"):
            self._row = max(self._row, 0)  # cells without a <tr>
            a = dict(attrs)
            rs, cs = _span(a.get("rowspan")), _span(a.get("colspan"))
            while (self._row, self._col) in self._busy:
                self._col += 1
            self._cell = [self._row, self._col, rs, cs, []]
            self.tables[-1].append(self._cell)
            for r in range(self._row, self._row + rs):
                for c in range(self._col, self._col + cs):
                    self._busy.add((r, c))
            self._col += cs
        elif tag == "br" and self._cell is not None:
            self._cell[4].append(" ")

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth = max(self._depth - 1, 0)
            if self._depth == 0:
                self._cell = None
        elif tag in ("td", "th") and self._depth == 1:
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[4].append(data)


def _span(v: str | None) -> int:
    digits = re.sub(r"\D", "", v or "")
    return max(int(digits), 1) if digits else 1


def table_cells(html: str) -> list[list[Cell]]:
    """Cells of each top-level table in `html`; cell text is whitespace-collapsed."""
    p = _TableParser()
    p.feed(html or "")
    p.close()
    return [[(r, c, rs, cs, " ".join("".join(t).split())) for r, c, rs, cs, t in cells] for cells in p.tables]


def to_grid(cells: list[Cell]) -> tuple[list[list[str]], list[dict]]:
    """Dense grid (text in the top-left cell of a merge) and spans. Spans past the last row are clipped."""
    if not cells:
        return [], []
    nrows = max(r for r, *_ in cells) + 1
    ncols = max(c + cs for _, c, _, cs, _ in cells)
    grid = [["" for _ in range(ncols)] for _ in range(nrows)]
    spans = []
    for r, c, rs, cs, text in cells:
        rs = min(rs, nrows - r)
        if not grid[r][c]:
            grid[r][c] = text
        if rs > 1 or cs > 1:
            spans.append({"row": r, "col": c, "rowspan": rs, "colspan": cs})
    return grid, spans
