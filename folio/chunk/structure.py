"""Document -> atomic leaves -> structure tree (sections; slide/sheet/page nodes when there are no headings)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from folio.contracts import Document

ATOMIC = {"paragraph", "list", "table", "kv", "figure", "code"}


@dataclass(eq=False)
class Leaf:
    kind: str  # paragraph, list, table, kv, figure, code, heading
    text: str
    page: int
    start: str  # "p{page}b{index}" of the first block
    end: str  # same for the last block (differs when a caption is attached)
    bbox: list[float] | None
    sp: list[str]
    level: int | None = None
    path: list[str] = field(default_factory=list)  # section path of the tree node holding it
    header: str = ""  # table: GFM header (+ separator); `atoms` are (row-unit text, first row, last row)
    atoms: list[tuple[str, int, int]] = field(default_factory=list)
    pre: str = ""  # caption before / after the leaf
    post: str = ""


@dataclass(eq=False)
class Node:
    key: Any  # str section title, or ("pg", n) for a slide/sheet/page node without headings
    path: list[str]
    heading: Leaf | None = None
    items: list[Any] = field(default_factory=list)  # Leaf | Node, document order
    _text: str | None = None

    @property
    def is_page(self) -> bool:
        return isinstance(self.key, tuple)

    def flat(self) -> list[Leaf]:
        out = [self.heading] if self.heading else []
        for it in self.items:
            out += it.flat() if isinstance(it, Node) else [it]
        return out

    @property
    def has_body(self) -> bool:
        return any(it.has_body if isinstance(it, Node) else True for it in self.items)

    def text(self) -> str:
        if self._text is None:
            parts = ([self.heading.text] if self.heading else []) + [it.text() if isinstance(it, Node) else it.text for it in self.items]
            self._text = "\n\n".join(p for p in parts if p)
        return self._text


def _cell(c: Any) -> str:
    return str(c).replace("\n", " ").replace("|", "\\|").strip()


def _table(cells: list[list[str]], spans) -> tuple[str, list[tuple[str, int, int]]]:
    """GFM header + row units. Rows tied by a rowspan (`spans`) form one unit; a spanning header row extends the header."""
    n = len(cells)
    width = max((len(r) for r in cells), default=0)
    rows = ["| " + " | ".join([_cell(c) for c in r] + [""] * (width - len(r))) + " |" for r in cells]
    reach = list(range(n))
    for s in spans or []:
        if s.row < n:
            reach[s.row] = max(reach[s.row], min(n - 1, s.row + s.rowspan - 1))
    units, a = [], 0
    while a < n:
        b, i = a, a
        while i <= b:
            b = max(b, reach[i])
            i += 1
        units.append((a, b))
        a = b + 1
    if not units:
        return "", []
    h_end = units[0][1]
    sep = "| " + " | ".join(["---"] * width) + " |"
    header = "\n".join([rows[0], sep, *rows[1 : h_end + 1]])
    return header, [("\n".join(rows[a : b + 1]), a, b) for a, b in units[1:]]


def build_leaves(doc: Document, include_header_footer: bool) -> list[Leaf]:
    leaves: list[Leaf] = []
    pre: tuple[str, str] | None = None  # caption waiting for the figure/table that follows it
    for pi, pg in enumerate(doc.pages):
        pno = pg.page or pi + 1
        blocks = [(i, b) for i, b in enumerate(pg.blocks) if include_header_footer or b.type not in ("header", "footer")]
        for k, (i, b) in enumerate(blocks):
            pos = f"p{pno}b{i}"
            kind = b.type
            if kind == "caption":
                text = (b.text or "").strip()
                if not text:
                    continue
                prev = leaves[-1] if leaves else None
                if prev and prev.page == pno and prev.kind in ("figure", "table") and not prev.post:
                    prev.post, prev.end = text, pos
                    continue
                if k + 1 < len(blocks) and blocks[k + 1][1].type in ("figure", "table"):
                    pre = (text, pos)
                    continue
                kind = "paragraph"
            header, atoms = "", []
            if kind == "table":
                header, atoms = _table(b.cells or [], b.spans)
                text = "\n".join([header, *[a[0] for a in atoms]]) if header else ""
            elif kind == "kv":
                text = f"{b.key}: {b.value}"
            else:
                text = b.text or ""
            text = text.strip("\n") if kind == "code" else text.strip()
            leaf = Leaf(kind, text, pno, pos, pos, b.bbox, list(b.section_path), b.level, header=header, atoms=atoms)
            if kind in ("figure", "table") and pre:
                leaf.pre, leaf.start, pre = pre[0], pre[1], None
            leaves.append(leaf)
    for lf in leaves:  # captions are part of the leaf text
        if lf.kind in ("figure", "table"):
            lf.text = "\n".join(p for p in (lf.pre, lf.text, lf.post) if p)
    return [lf for lf in leaves if lf.text.strip()]


def build_tree(leaves: list[Leaf]) -> Node:
    """Headings (level, else section_path) and `section_path` define sections; heading-less leaves go under a page node."""
    root = Node(None, [])
    stack = [root]

    def keys() -> list[Any]:
        return [n.key for n in stack[1:]]

    def align(target: list[Any], new_last: bool = False) -> None:
        cur = keys()
        k = 0
        while k < min(len(cur), len(target) - (1 if new_last else 0)) and cur[k] == target[k]:
            k += 1
        del stack[k + 1 :]
        for key in target[k:]:
            node = Node(key, stack[-1].path + ([key] if isinstance(key, str) else []))
            stack[-1].items.append(node)
            stack.append(node)

    for lf in leaves:
        if lf.kind == "heading":
            sects = [k for k in keys() if isinstance(k, str)]
            if lf.sp and lf.sp[-1] == lf.text:
                target = list(lf.sp)
            elif lf.level:
                target = sects[: lf.level - 1] + [lf.text]
            else:
                target = list(lf.sp) + [lf.text]
            align(target, new_last=True)
            stack[-1].heading = lf
            lf.path = stack[-1].path
            continue
        if lf.sp:
            align(list(lf.sp))
        elif len(stack) > 1 and isinstance(stack[1].key, str):
            pass  # keep the current section
        else:
            align([("pg", lf.page)])
        lf.path = stack[-1].path
        stack[-1].items.append(lf)
    return root
