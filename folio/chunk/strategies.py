"""Chunking strategies. Every strategy flattens blocks to units, groups them, and packs groups under max_tokens."""

from __future__ import annotations

from typing import Any

from folio.chunk.tokens import atomize, pack, resolve_counter
from folio.contracts import Document, make_chunk_id
from folio.errors import ConfigError

STRATEGIES = ("section", "table", "page", "fixed")


def _render_table(cells: list[list[str]]) -> list[str]:
    return [" | ".join(str(c).strip() for c in row) for row in cells]


def _units(doc: Document, include_header_footer: bool) -> list[dict]:
    units = []
    for pg, b in doc.iter_blocks():
        if b.type in ("header", "footer") and not include_header_footer:
            continue
        rows = None
        if b.type == "table":
            rows = _render_table(b.cells or [])
            text = "\n".join(rows)
        elif b.type == "kv":
            text = f"{b.key}: {b.value}"
        else:
            text = b.text or ""
        if not text.strip():
            continue
        units.append(
            {"text": text.strip(), "rows": rows, "type": b.type, "page": pg.page, "bbox": b.bbox, "section_path": list(b.section_path)}
        )
    return units


def _make(units: list[dict], text: str, ctype: str) -> dict:
    first = units[0]
    pages = sorted({u["page"] for u in units if u["page"] is not None})
    boxes = [u["bbox"] for u in units if u["bbox"] and u["page"] == first["page"]]
    bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None
    return {"text": text, "units": units, "page": first["page"], "pages": pages, "bbox": bbox, "section_path": first["section_path"], "type": ctype}


def _pack_units(units: list[dict], ctype: str, o: dict) -> list[dict]:
    """Pack consecutive units (joined by blank lines) into chunks <= max_tokens."""
    atoms: list[tuple[str, dict]] = []
    for u in units:
        parts = atomize(u["text"], o["max_tokens"], o["count"])
        for k, p in enumerate(parts):
            atoms.append((p + ("\n\n" if k == len(parts) - 1 else ""), u))
    out = []
    for win in pack(atoms, o["max_tokens"], o["overlap"], o["count"]):
        text = "".join(a[0] for a in win).strip()
        seen: list[dict] = []
        for _, u in win:
            if not any(u is s for s in seen):
                seen.append(u)
        if text:
            out.append(_make(seen, text, ctype))
    return out


def _table_chunks(u: dict, o: dict) -> list[dict]:
    rows, mx, count = u["rows"], o["max_tokens"], o["count"]
    if count(u["text"]) <= mx:
        return [_make([u], u["text"], "table")]
    header = rows[0] if rows else ""
    hsize = count(header)
    if len(rows) < 2 or hsize * 2 >= mx:
        return _pack_units([u], "table", o)
    groups: list[list[str]] = []
    cur: list[str] = []
    for row in rows[1:]:
        if count("\n".join([header, *cur, row])) <= mx:
            cur.append(row)
            continue
        if cur:
            groups.append(cur)
            cur = []
        if count("\n".join([header, row])) <= mx:
            cur = [row]
        else:  # single row too large: split the row itself, header repeated on every piece
            groups.extend([p.strip()] for p in atomize(row, mx - hsize, count) if p.strip())
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        text = "\n".join([header, *g])
        if count(text) > mx:  # custom counter safety net
            out.extend(_pack_units([{**u, "text": text}], "table", o))
        else:
            out.append(_make([u], text, "table"))
    return out


def _by_key(units: list[dict], key) -> list[list[dict]]:
    groups: list[list[dict]] = []
    last: Any = object()
    for u in units:
        k = key(u)
        if not groups or k != last:
            groups.append([])
            last = k
        groups[-1].append(u)
    return groups


def _text_and_tables(units: list[dict], text_type: str, o: dict, key) -> list[dict]:
    chunks: list[dict] = []
    run: list[dict] = []

    def flush():
        for g in _by_key(run, key):
            chunks.extend(_pack_units(g, text_type, o))
        run.clear()

    for u in units:
        if u["type"] == "table":
            flush()
            chunks.extend(_table_chunks(u, o))
        else:
            run.append(u)
    flush()
    return chunks


def _merge_small(chunks: list[dict], o: dict) -> list[dict]:
    if not o["min_tokens"]:
        return chunks
    out: list[dict] = []
    for c in chunks:
        if out and c["type"] != "table" and out[-1]["type"] != "table" and o["count"](c["text"]) < o["min_tokens"]:
            text = out[-1]["text"] + "\n\n" + c["text"]
            if o["count"](text) <= o["max_tokens"]:
                out[-1] = _make(out[-1]["units"] + c["units"], text, out[-1]["type"])
                continue
        out.append(c)
    # a trailing/leading small chunk may remain if it cannot be merged without exceeding max_tokens
    return out


def _build(doc: Document, strategy: str, opts: dict) -> list[dict]:
    o = {
        "max_tokens": int(opts.get("max_tokens", 512)),
        "overlap": int(opts.get("overlap_tokens", 0)),
        "min_tokens": int(opts.get("min_tokens", 0)),
        "count": resolve_counter(opts.get("tokenizer")),
    }
    if o["max_tokens"] < 1 or not 0 <= o["overlap"] < o["max_tokens"]:
        raise ConfigError("chunk: need max_tokens >= 1 and 0 <= overlap_tokens < max_tokens")
    units = _units(doc, bool(opts.get("include_header_footer", False)))
    if strategy == "section":
        chunks = _text_and_tables(units, "section", o, lambda u: u["section_path"])
    elif strategy == "table":
        chunks = _text_and_tables(units, "text", o, lambda u: u["section_path"])
    elif strategy == "page":
        chunks = []
        for g in _by_key(units, lambda u: u["page"]):
            chunks.extend(_pack_units(g, "page", o))
    elif strategy == "fixed":
        chunks = _pack_units(units, "fixed", o)
    else:
        raise ConfigError(f"unknown chunk strategy {strategy!r}; expected one of {STRATEGIES}")
    chunks = _merge_small(chunks, o)
    return [
        {
            "doc_id": doc.doc_id,
            "chunk_id": make_chunk_id(doc.doc_id, i, c["text"]),
            "content": c["text"],
            "page": c["page"],
            "section_path": c["section_path"],
            "source_uri": doc.source_uri,
            "chunk_type": c["type"],
            "bbox": c["bbox"],
            "pages": c["pages"],
            "chunk_strategy": strategy,
        }
        for i, c in enumerate(chunks)
    ]


def choose_strategy(doc: Document, include_header_footer: bool = False) -> str:
    blocks = [b for _, b in doc.iter_blocks() if include_header_footer or b.type not in ("header", "footer")]
    blocks = [b for b in blocks if b.type == "table" or (b.text or b.key or "").strip()]
    if any(b.type == "heading" for b in blocks):
        return "section"
    if blocks and sum(b.type == "table" for b in blocks) / len(blocks) >= 0.5:
        return "table"
    if len(doc.pages) > 1:
        return "page"
    return "fixed"


def auto(doc: Document, **opts) -> list[dict]:
    strategy = opts.pop("strategy", None) or choose_strategy(doc, bool(opts.get("include_header_footer", False)))
    return _build(doc, strategy, opts)


def section(doc: Document, **opts) -> list[dict]:
    return _build(doc, "section", opts)


def table(doc: Document, **opts) -> list[dict]:
    return _build(doc, "table", opts)


def page(doc: Document, **opts) -> list[dict]:
    return _build(doc, "page", opts)


def fixed(doc: Document, **opts) -> list[dict]:
    return _build(doc, "fixed", opts)
