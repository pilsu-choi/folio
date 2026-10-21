"""Markdown / TXT native parsing (FR-29) and the Markdown -> blocks converter shared with VLM output."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt

from folio.parse.assemble import apply_sections, table_text
from folio.parse.html_table import table_cells, to_grid

_MD = MarkdownIt("commonmark").enable("table")


def decode(data: bytes) -> tuple[str, str]:
    """bytes -> (text, encoding). BOM -> UTF-8 -> CP949 (covers EUC-KR) -> charset-normalizer -> latin-1."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-sig"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    for enc in ("utf-8", "cp949"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is not None:
            return str(best), best.encoding
    except ImportError:
        pass
    return data.decode("latin-1"), "latin-1"


def parse_txt(text: str) -> list[dict]:
    parts = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    return [{"type": "paragraph", "text": p.strip("\n").rstrip(), "text_source": "native"} for p in parts if p.strip()]


def _inline(tok) -> tuple[str, list[str]]:
    """inline token -> (text, image alt texts)."""
    out, imgs = [], []
    for c in tok.children or []:
        if c.type == "image":
            imgs.append(c.content)
        elif c.type in ("softbreak", "hardbreak"):
            out.append("\n")
        elif c.type in ("text", "code_inline"):
            out.append(c.content)
        elif c.type == "html_inline":
            continue
    return "".join(out).strip(), imgs


_TABLE_TAG = re.compile(r"<(/?)table\b[^>]*>", re.I)
_FENCED_TABLE = re.compile(r"```[\w-]*[ \t]*\n(\s*<table\b.*?</table>\s*)\n?```", re.I | re.S)
_SLOT = "FOLIOHTMLTABLE{}"


def _lift_tables(md: str) -> tuple[str, list[str]]:
    """Cut top-level <table>...</table> out of `md` (also when fenced as ```html) and leave a placeholder
    paragraph, so blank lines inside a table do not split it into several Markdown blocks."""
    md = _FENCED_TABLE.sub(lambda m: m.group(1), md)
    out, tables, depth, start, last = [], [], 0, 0, 0
    for m in _TABLE_TAG.finditer(md):
        if not m.group(1):
            if depth == 0:
                start = m.start()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                out.append(md[last:start] + f"\n\n{_SLOT.format(len(tables))}\n\n")
                tables.append(md[start:m.end()])
                last = m.end()
    out.append(md[last:])
    return "".join(out), tables


def md_blocks(md: str, source: str = "native", box: list[float] | None = None) -> list[dict]:
    """Markdown -> block dicts (no section_path). `box` is used as bbox for every block (VLM output).
    HTML tables become table blocks with `spans` (rowspan/colspan)."""
    md, html = _lift_tables(md)
    toks = _MD.parse(md)
    blocks: list[dict] = []

    def add(b: dict) -> None:
        b["text_source"] = source
        if box is not None:
            b["bbox"] = list(box)
        blocks.append(b)

    i = 0
    while i < len(toks):
        t = toks[i]
        if t.type == "heading_open":
            text, _ = _inline(toks[i + 1])
            add({"type": "heading", "text": text, "level": int(t.tag[1])})
            i += 3
        elif t.type == "paragraph_open" and (m := re.fullmatch(_SLOT.format(r"(\d+)"), toks[i + 1].content.strip())):
            for cells in table_cells(html[int(m.group(1))]):
                grid, spans = to_grid(cells)
                if grid:
                    add({"type": "table", "text": table_text(grid), "cells": grid, **({"spans": spans} if spans else {})})
            i += 3
        elif t.type == "paragraph_open":
            text, imgs = _inline(toks[i + 1])
            if text:
                add({"type": "paragraph", "text": text})
            for alt in imgs:
                add({"type": "figure", "text": alt})
            i += 3
        elif t.type in ("fence", "code_block"):
            add({"type": "code", "text": t.content.rstrip("\n")})
            i += 1
        elif t.type in ("bullet_list_open", "ordered_list_open"):
            j, depth = i, 0
            lines: list[str] = []
            counters: list[int] = []
            while True:
                u = toks[j]
                if u.type in ("bullet_list_open", "ordered_list_open"):
                    depth += 1
                    counters.append(int(u.attrGet("start") or 1) if u.type == "ordered_list_open" else 0)
                elif u.type in ("bullet_list_close", "ordered_list_close"):
                    depth -= 1
                    counters.pop()
                    if depth == 0:
                        break
                elif u.type == "list_item_open":
                    pass
                elif u.type == "inline":
                    text, imgs = _inline(u)
                    marker = f"{counters[-1]}. " if counters[-1] else "- "
                    if toks[j - 1].type in ("paragraph_open", "list_item_open"):
                        if toks[j - 1].type == "paragraph_open" and toks[j - 2].type != "list_item_open":
                            lines.append("  " * (depth - 1) + "  " + (text or ""))  # continuation paragraph
                        else:
                            lines.append("  " * (depth - 1) + marker + (text or "".join(imgs)))
                            if counters[-1]:
                                counters[-1] += 1
                elif u.type in ("fence", "code_block"):
                    lines.append("  " * depth + u.content.rstrip("\n"))
                j += 1
            add({"type": "list", "text": "\n".join(lines)})
            i = j + 1
        elif t.type == "table_open":
            rows: list[list[str]] = []
            j = i
            while toks[j].type != "table_close":
                if toks[j].type == "tr_open":
                    rows.append([])
                elif toks[j].type == "inline":
                    rows[-1].append(" ".join(_inline(toks[j])[0].split()))
                j += 1
            n = max((len(r) for r in rows), default=0)
            rows = [r + [""] * (n - len(r)) for r in rows]
            add({"type": "table", "text": table_text(rows), "cells": rows})
            i = j + 1
        elif t.type == "html_block":
            text = re.sub(r"<!--.*?-->", "", t.content, flags=re.S).strip()
            if text:
                add({"type": "paragraph", "text": text})
            i += 1
        else:  # blockquote_*, hr, ... : container tokens are transparent
            i += 1
    return blocks


def parse_text(data: bytes, mime: str) -> tuple[list[dict], dict]:
    """-> (pages, extra meta). One page, no size, blocks native."""
    text, enc = decode(data)
    blocks = md_blocks(text) if mime == "text/markdown" else parse_txt(text)
    apply_sections(blocks)
    return [{"page": 1, "width": None, "height": None, "blocks": blocks, "quality": None}], {"encoding": enc}
