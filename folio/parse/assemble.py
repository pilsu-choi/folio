"""Match text atoms to structure regions (by coordinates) and build Document-JSON block dicts."""

from __future__ import annotations

import re
import statistics

from folio.backends.base import PageStructure
from folio.parse.textlayer import Atom, Box, union

BLOCK_TYPES = {"heading", "paragraph", "list", "table", "kv", "figure", "caption", "header", "footer"}


def _area(b: Box) -> float:
    return max(b[2] - b[0], 0.0) * max(b[3] - b[1], 0.0)


def _inter(a: Box, b: Box) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _inside(pt: tuple[float, float], b: Box) -> bool:
    return b[0] <= pt[0] <= b[2] and b[1] <= pt[1] <= b[3]


def scale_box(b, k: float) -> Box:
    return (b[0] * k, b[1] * k, b[2] * k, b[3] * k)


def cluster_lines(atoms: list[Atom]) -> list[Atom]:
    """Group atoms without line info (OCR) into lines by vertical overlap; returns atoms in reading order."""
    lines: list[list[Atom]] = []
    ycs: list[float] = []
    for a in sorted(atoms, key=lambda a: a.center[1]):
        h = a.bbox[3] - a.bbox[1]
        for i, line in enumerate(lines):
            lh = statistics.median(x.bbox[3] - x.bbox[1] for x in line)
            if abs(a.center[1] - ycs[i]) <= 0.5 * min(h, lh):
                line.append(a)
                ycs[i] = statistics.mean(x.center[1] for x in line)
                break
        else:
            lines.append([a])
            ycs.append(a.center[1])
    out: list[Atom] = []
    for n, i in enumerate(sorted(range(len(lines)), key=lambda i: ycs[i]), 1):
        for a in sorted(lines[i], key=lambda a: a.bbox[0]):
            a.line = n
            a.size = a.size or (a.bbox[3] - a.bbox[1])
            out.append(a)
    return out


def join_text(atoms: list[Atom], char_mode: bool) -> str:
    lines: dict[int, list[str]] = {}
    for a in atoms:
        lines.setdefault(a.line, []).append(a.text)
    sep = "" if char_mode else " "
    return "\n".join(t for t in (sep.join(v).strip() for v in lines.values()) if t)


def _group_orphans(atoms: list[Atom]) -> list[list[Atom]]:
    """Paragraph groups for atoms outside every region: PyMuPDF block + vertical line proximity."""
    lines: dict[int, list[Atom]] = {}
    for a in atoms:
        lines.setdefault(a.line, []).append(a)
    groups: list[list[Atom]] = []
    prev: list[Atom] | None = None
    prev_box: Box | None = None
    for items in lines.values():
        box = union(a.bbox for a in items)
        h = max(box[3] - box[1], 1.0)
        if prev is not None and prev[-1].block == items[0].block and prev_box is not None \
                and box[1] - prev_box[3] <= 0.8 * h and box[1] >= prev_box[1] - 0.5 * h:
            prev.extend(items)
            prev_box = union([prev_box, box])
        else:
            prev = list(items)
            groups.append(prev)
            prev_box = box
    return groups


def _grid(cells, atoms_by_cell: dict[int, list[Atom]], char_mode: bool):
    nrows = max((c.row + c.rowspan for c in cells), default=0)
    ncols = max((c.col + c.colspan for c in cells), default=0)
    grid = [["" for _ in range(ncols)] for _ in range(nrows)]
    spans = []
    for i, c in enumerate(cells):
        text = join_text(atoms_by_cell.get(i, []), char_mode).replace("\n", " ") if atoms_by_cell.get(i) else (c.text or "")
        if text and not grid[c.row][c.col]:
            grid[c.row][c.col] = text
        if c.rowspan > 1 or c.colspan > 1:
            spans.append({"row": c.row, "col": c.col, "rowspan": c.rowspan, "colspan": c.colspan})
    return grid, spans


def table_text(grid: list[list[str]]) -> str:
    return "\n".join(" | ".join(r) for r in grid)


_KV = re.compile(r"^(.{1,40}?)\s*[:：]\s*(.+)$", re.DOTALL)


def build_blocks(atoms: list[Atom], struct: PageStructure, scale: float, source: str, char_mode: bool = False) -> list[dict]:
    """atoms: reading-ordered text atoms (pt). struct: backend result in px; `scale` px -> pt."""
    containers: list[dict] = []  # table containers first
    for t in struct.tables:
        tb = scale_box(t.bbox, scale)
        containers.append({"kind": "table", "box": tb, "table": t, "atoms": []})
    for r in struct.regions:
        rb = scale_box(r.bbox, scale)
        if r.type == "table":
            if any(_inter(rb, c["box"]) > 0.5 * min(_area(rb), _area(c["box"])) for c in containers if c["kind"] == "table"):
                continue
            containers.append({"kind": "table", "box": rb, "table": None, "atoms": []})
        else:
            containers.append({"kind": "region", "box": rb, "region": r, "atoms": []})

    order = [i for i, c in enumerate(containers) if c["kind"] == "table"] + \
            [i for i, c in enumerate(containers) if c["kind"] != "table"]
    orphans: list[Atom] = []
    for a in atoms:
        best = None
        for tier in ("table", "region"):
            cand = [i for i in order if (containers[i]["kind"] == "table") == (tier == "table")
                    and _inside(a.center, containers[i]["box"])]
            if cand:
                best = min(cand, key=lambda i: _area(containers[i]["box"]))
                break
        if best is None and _area(a.bbox) > 0:
            ov = [(_inter(a.bbox, containers[i]["box"]) / _area(a.bbox), -j, i) for j, i in enumerate(order)]
            if ov:
                frac, _, i = max(ov)
                if frac >= 0.5:
                    best = i
        if best is None:
            orphans.append(a)
        else:
            containers[best]["atoms"].append(a)

    blocks: list[dict] = []
    for c in containers:
        at = c["atoms"]
        if c["kind"] == "table":
            blocks.append(_table_block(c, at, source, char_mode, scale))
            continue
        r = c["region"]
        typ = r.type if r.type in BLOCK_TYPES else "paragraph"
        if not at:
            if typ == "figure":
                blocks.append({"type": "figure", "bbox": list(c["box"]), "text_source": source})
            continue
        text = join_text(at, char_mode)
        if not text:
            continue
        b = {"type": typ, "text": text, "bbox": list(union(a.bbox for a in at)), "text_source": source,
             "_size": statistics.median(a.size for a in at if a.size) if any(a.size for a in at) else 0.0}
        if typ == "heading" and r.level:
            b["level"] = r.level
        if typ == "kv":
            k, v = r.key, r.value
            if k is None or v is None:
                m = _KV.match(text)
                k, v = (m.group(1), m.group(2)) if m else (None, None)
            if k is None:
                b["type"] = "paragraph"
            else:
                b["key"], b["value"] = k, v
        blocks.append(b)
    for grp in _group_orphans(orphans):
        text = join_text(grp, char_mode)
        if text:
            sizes = [a.size for a in grp if a.size]
            blocks.append({"type": "paragraph", "text": text, "bbox": list(union(a.bbox for a in grp)),
                           "text_source": source, "_size": statistics.median(sizes) if sizes else 0.0})
    blocks.sort(key=lambda b: (round(b["bbox"][1]), b["bbox"][0]))
    return blocks


def _table_block(c: dict, atoms: list[Atom], source: str, char_mode: bool, scale: float) -> dict:
    t = c["table"]
    if t is None or not t.cells:
        text = join_text(atoms, char_mode)
        grid = [[ln] for ln in text.split("\n")] if text else [[""]]
        bbox = list(union(a.bbox for a in atoms)) if atoms else list(c["box"])
        return {"type": "table", "text": table_text(grid), "bbox": bbox, "text_source": source, "cells": grid}
    cell_boxes = [scale_box(x.bbox, scale) for x in t.cells]
    by_cell: dict[int, list[Atom]] = {}
    for a in atoms:
        cand = [i for i, b in enumerate(cell_boxes) if _inside(a.center, b)]
        if cand:
            i = min(cand, key=lambda i: _area(cell_boxes[i]))
        else:
            i = min(range(len(cell_boxes)), key=lambda i: _dist(a.center, cell_boxes[i]))
        by_cell.setdefault(i, []).append(a)
    grid, spans = _grid(t.cells, by_cell, char_mode)
    b = {"type": "table", "text": table_text(grid), "bbox": list(c["box"]), "text_source": source, "cells": grid}
    if spans:
        b["spans"] = spans
    return b


def _dist(pt, b: Box) -> float:
    dx = max(b[0] - pt[0], 0, pt[0] - b[2])
    dy = max(b[1] - pt[1], 0, pt[1] - b[3])
    return dx * dx + dy * dy


def finalize(pages_blocks: list[list[dict]]) -> None:
    """Heading levels (font-size ranking when the region has none) and `section_path`; strips private keys."""
    heads = [b for blocks in pages_blocks for b in blocks if b["type"] == "heading" and not b.get("level")]
    sizes = sorted({round(b["_size"] * 2) / 2 for b in heads if b.get("_size")}, reverse=True)
    for b in heads:
        s = round(b.get("_size", 0) * 2) / 2
        b["level"] = sizes.index(s) + 1 if s in sizes else 1
    stack: list[tuple[int, str]] = []
    for blocks in pages_blocks:
        for b in blocks:
            b["section_path"] = [t for _, t in stack]
            if b["type"] == "heading":
                lvl = b.get("level") or 1
                while stack and stack[-1][0] >= lvl:
                    stack.pop()
                stack.append((lvl, b["text"].replace("\n", " ")))
            b.pop("_size", None)
            b["bbox"] = [round(x, 2) for x in b["bbox"]]
