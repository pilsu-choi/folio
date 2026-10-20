"""Chunking strategies: `adaptive` (default), `page`, `fixed`. See PRD "청킹".

Size rule: ``max_tokens`` is enforced on ``contextualized_content`` (``" > ".join(section_path)`` + blank line +
``content``), because that is what gets embedded. If the section path alone exceeds half of ``max_tokens`` it is
ignored for sizing. ``fixed`` ignores structure: its ``contextualized_content`` equals ``content``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from folio.chunk.structure import Leaf, Node, build_leaves, build_tree
from folio.chunk.tokens import atomize, pack, resolve_counter, sentences
from folio.contracts import Document, make_chunk_id, settings_hash, validate_chunk_record
from folio.errors import ConfigError

DEFAULTS: dict[str, Any] = {"max_tokens": 1024, "min_tokens": 128, "tokenizer": None, "overlap": 0, "include_header_footer": False}
_UNIT = {"paragraph": "s", "kv": "s", "figure": "s", "list": "i", "code": "l", "table": "r"}
Atom = tuple[str, int, int, "int | None"]  # text, first ordinal, last ordinal, window index of a forced split


def _join(a: str, b: str) -> str:
    return f"{a}\n\n{b}" if a and b else a or b


def _common(a: list[str], b: list[str]) -> list[str]:
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return a[:n]


@dataclass(eq=False)
class Chunk:
    text: str
    path: list[str]
    type: str
    parts: list[tuple[Leaf, str | None]]  # leaf and the sub-range of it that is in this chunk (None = all)
    sealed: bool = False  # chunks of a descended section never merge with outside siblings


@dataclass
class Lead:
    """Headings collected so far that belong to the next body chunk."""

    text: str = ""
    leaves: list[Leaf] = field(default_factory=list)

    def take(self) -> list[tuple[Leaf, str | None]]:
        parts = [(lf, None) for lf in self.leaves]
        self.text, self.leaves = "", []
        return parts


class Chunker:
    def __init__(self, doc: Document, strategy: str, opts: dict[str, Any]):
        unknown = set(opts) - set(DEFAULTS)
        if unknown:
            raise ConfigError(f"chunk: unknown option(s) {sorted(unknown)}; expected {sorted(DEFAULTS)}")
        o = {**DEFAULTS, **opts}
        self.max, self.min, self.overlap = int(o["max_tokens"]), int(o["min_tokens"]), int(o["overlap"])
        if self.max < 1 or self.min < 0 or not 0 <= self.overlap < self.max:
            raise ConfigError("chunk: need max_tokens >= 1, min_tokens >= 0 and 0 <= overlap < max_tokens")
        tok = o["tokenizer"]
        self.opts = {**o, "tokenizer": tok if tok is None or isinstance(tok, str) else getattr(tok, "__qualname__", repr(tok))}
        raw = resolve_counter(tok)
        self._cache: dict[str, int] = {}

        def count(text: str) -> int:
            if text not in self._cache:
                self._cache[text] = raw(text)
            return self._cache[text]

        self.count = count
        self.doc, self.strategy = doc, strategy
        self.settings = settings_hash(strategy, self.opts)
        self.leaves = build_leaves(doc, bool(o["include_header_footer"]))

    # ---- sizing -------------------------------------------------------------------------------------------------
    def prefix(self, path: list[str]) -> str:
        p = " > ".join(path) + "\n\n" if path else ""
        return p if self.count(p) <= self.max // 2 else ""

    def fits(self, text: str, path: list[str]) -> bool:
        return self.count(self.prefix(path) + text) <= self.max

    # ---- forced token windows (last resort; the only place `overlap` is applied inside adaptive/page) ------------
    def windows(self, text: str, path: list[str], fit: Callable[[str], bool], reserve: str) -> list[str]:
        b = max(1, self.max - self.count(self.prefix(path) + reserve))
        while True:
            atoms = [(a, None) for a in atomize(text, b, self.count)]
            wins = [w for win in pack(atoms, b, min(self.overlap, b // 2), self.count) if (w := "".join(a[0] for a in win).strip())]
            if b == 1 or all(fit(w) for w in wins):
                return wins
            b = min(b - 1, b * 4 // 5)

    # ---- leaf splitting -------------------------------------------------------------------------------------------
    def wide(self, leaf: Leaf) -> bool:
        """Table header too big to repeat in every piece: it is split like body rows instead."""
        return self.count(leaf.header) > self.max // 2

    def atoms_of(self, leaf: Leaf) -> tuple[list[Atom], str, Callable[[Atom], list[Atom]] | None]:
        if leaf.kind == "table" and leaf.atoms:
            rows = [(t, a, b, None) for t, a, b in leaf.atoms]
            if self.wide(leaf):  # header (with caption) becomes the first rows; no per-piece header
                t, a, b, _ = rows[-1]
                rows[-1] = ("\n".join(p for p in (t, leaf.post) if p), a, b, None)
                rows.insert(0, ("\n".join(p for p in (leaf.pre, leaf.header) if p), 0, leaf.atoms[0][1] - 1, None))
            return rows, "\n", None
        if leaf.kind == "list":
            lines = [ln for ln in leaf.text.split("\n") if ln.strip()]
            base = min(len(ln) - len(ln.lstrip()) for ln in lines)
            items: list[list[str]] = []
            for ln in lines:  # deeper-indented lines (sub-items, continuations) stay with the previous item
                if not items or len(ln) - len(ln.lstrip()) <= base:
                    items.append([])
                items[-1].append(ln)
            return [("\n".join(it), i + 1, i + 1, None) for i, it in enumerate(items)], "\n", None
        if leaf.kind == "code":
            lines = leaf.text.split("\n")
            segs, a = [], 0
            while a < len(lines):  # a run of code lines plus the blank lines after it
                b = a
                while b < len(lines) and lines[b].strip():
                    b += 1
                while b < len(lines) and not lines[b].strip():
                    b += 1
                segs.append(("\n".join(lines[a:b]), a + 1, b, None))
                a = b

            def by_line(at: Atom) -> list[Atom]:
                return [(ln, at[1] + i, at[1] + i, None) for i, ln in enumerate(at[0].split("\n"))] if at[1] != at[2] else []

            return segs, "\n", by_line
        return [(s, i + 1, i + 1, None) for i, s in enumerate(sentences(leaf.text))], "", None

    def split(self, leaf: Leaf, path: list[str], lead: str) -> list[tuple[str, str]]:
        """Pieces (text, sub-range) of a leaf that does not fit as a whole. `lead` (headings) rides on piece 0."""
        lead = lead if lead and self.fits(lead + "\n\nx", path) else ""
        table = leaf.kind == "table" and bool(leaf.atoms) and not self.wide(leaf)
        wrap = (lambda t, pre=True, post=True: "\n".join(p for p in ((leaf.pre if pre else ""), leaf.header, t, (leaf.post if post else "")) if p)) if table else (lambda t, pre=True, post=True: t)
        atoms, sep, refine = self.atoms_of(leaf)
        strip = (lambda t: t.strip("\n")) if leaf.kind in ("code", "list", "table") else str.strip

        def fit(t: str) -> bool:
            return self.fits(_join(lead, strip(wrap(t))), path)

        def settle(items: list[Atom]) -> list[Atom]:
            out: list[Atom] = []
            for at in items:
                if fit(at[0]):
                    out.append(at)
                elif (sub := refine(at) if refine else []):
                    out += settle(sub)
                else:
                    reserve = _join(lead, wrap("", False, False))
                    out += [(w, at[1], at[2], k) for k, w in enumerate(self.windows(at[0], path, fit, reserve))]
            return out

        atoms = settle(atoms)
        groups: list[list[Atom]] = []
        cur: list[Atom] = []
        for at in atoms:
            cand = cur + [at]
            t = strip(wrap(sep.join(x[0] for x in cand)))
            if cur and not self.fits(_join(lead if not groups else "", t), path):
                groups.append(cur)
                cur = [at]
            else:
                cur = cand
        if cur:
            groups.append(cur)
        u, out = _UNIT[leaf.kind], []
        for k, g in enumerate(groups):
            text = strip(wrap(sep.join(x[0] for x in g), k == 0, k == len(groups) - 1))
            first, last = g[0], g[-1]
            if len(g) == 1 and first[3] is not None:
                sub = f"{u}{first[1]}.{first[3]}"
            else:
                sub = f"{u}{first[1]}" if first[1] == last[2] else f"{u}{first[1]}-{last[2]}"
            out.append((text, sub))
        return out

    # ---- adaptive walk ------------------------------------------------------------------------------------------
    def merge(self, chunks: list[Chunk]) -> list[Chunk]:
        out: list[Chunk] = []
        for c in chunks:
            if out and not out[-1].sealed and not c.sealed:
                p = out[-1]
                if min(self.count(p.text), self.count(c.text)) < self.min:
                    path, text = _common(p.path, c.path), p.text + "\n\n" + c.text
                    if self.fits(text, path):
                        out[-1] = Chunk(text, path, "section", p.parts + c.parts)
                        continue
            out.append(c)
        return out

    def leaf_chunks(self, leaf: Leaf, node: Node, lead: Lead) -> list[Chunk]:
        text = _join(lead.text, leaf.text)
        if self.fits(text, node.path):
            return [Chunk(text, node.path, leaf.kind, lead.take() + [(leaf, None)])]
        pieces = self.split(leaf, node.path, lead.text)
        head, parts = lead.text, lead.take()
        return [
            Chunk(_join(head, t) if k == 0 else t, node.path, leaf.kind, (parts if k == 0 else []) + [(leaf, sub)])
            for k, (t, sub) in enumerate(pieces)
        ]

    def walk(self, node: Node, lead: Lead) -> list[Chunk]:
        flat = node.flat()
        if not node.has_body:  # heading-only subtree: its headings go in front of the next body chunk
            lead.text, lead.leaves = _join(lead.text, node.text()), lead.leaves + flat
            return []
        text = _join(lead.text, node.text())
        if self.fits(text, node.path):
            return [Chunk(text, node.path, "page" if node.is_page else "section", lead.take() + [(lf, None) for lf in flat])]
        if node.heading:
            lead.text, lead.leaves = _join(lead.text, node.heading.text), lead.leaves + [node.heading]
        out: list[Chunk] = []
        for it in node.items:
            out += self.walk(it, lead) if isinstance(it, Node) else self.leaf_chunks(it, node, lead)
        out = self.merge(out)
        for c in out:
            c.sealed = True
        return out

    def finish(self, nodes: list[Node]) -> list[Chunk]:
        lead, out = Lead(), []
        for n in nodes:
            out += self.walk(n, lead)
        out = self.merge(out)
        if lead.leaves:  # trailing headings without any body
            if out and self.fits(_join(out[-1].text, lead.text), out[-1].path):
                out[-1].text = _join(out[-1].text, lead.text)
                out[-1].parts += lead.take()
            elif not out:
                out.append(Chunk(lead.text, lead.leaves[0].path, "section", lead.take()))
        return out

    def adaptive(self) -> list[Chunk]:
        return self.finish(build_tree(self.leaves).items)

    def pages(self) -> list[Chunk]:
        out: list[Chunk] = []
        groups: list[list[Leaf]] = []
        for lf in self.leaves:
            if not groups or groups[-1][0].page != lf.page:
                groups.append([])
            groups[-1].append(lf)
        for g in groups:
            root = build_tree(g)
            text = _join("", root.text())
            if self.fits(text, g[0].path):
                out.append(Chunk(text, g[0].path, "page", [(lf, None) for lf in g]))
            else:
                part = self.finish(root.items)
                for c in part:
                    c.sealed = True
                out += part
        return out

    def fixed(self) -> list[Chunk]:
        atoms: list[tuple[str, Leaf]] = []
        for lf in self.leaves:
            lf.path = lf.sp
            parts = atomize(lf.text, self.max, self.count)
            atoms += [(p + ("\n\n" if k == len(parts) - 1 else ""), lf) for k, p in enumerate(parts)]
        out = []
        for k, win in enumerate(pack(atoms, self.max, self.overlap, self.count)):
            text = "".join(a[0] for a in win).strip()
            seen: list[Leaf] = []
            for _, lf in win:
                if not any(lf is s for s in seen):
                    seen.append(lf)
            if text:
                out.append(Chunk(text, seen[0].path, "fixed", [(lf, f"w{k}" if i == 0 else None) for i, lf in enumerate(seen)]))
        return out

    # ---- records ------------------------------------------------------------------------------------------------
    def block_range(self, c: Chunk) -> str:
        (first, fsub), (last, lsub) = c.parts[0], c.parts[-1]
        if len(c.parts) == 1:
            return f"{first.start}#{fsub}" if fsub else f"{first.start}-{first.end}"
        return f"{first.start}{'#' + fsub if fsub else ''}-{last.end}{'#' + lsub if lsub else ''}"

    def records(self, chunks: list[Chunk]) -> list[dict]:
        out, seen = [], set()
        for c in chunks:
            leaves = [lf for lf, _ in c.parts]
            first = leaves[0]
            boxes = [lf.bbox for lf in leaves if lf.bbox and lf.page == first.page]
            bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None
            rng = self.block_range(c)
            while rng in seen:
                rng += "~"
            seen.add(rng)
            ctx = c.text if self.strategy == "fixed" or not c.path else f"{' > '.join(c.path)}\n\n{c.text}"
            rec = {
                "doc_id": self.doc.doc_id,
                "chunk_id": make_chunk_id(self.doc.doc_id, rng, self.settings),
                "content": c.text,
                "page": first.page,
                "section_path": list(c.path),
                "source_uri": self.doc.source_uri,
                "pages": sorted({lf.page for lf in leaves}),
                "chunk_type": c.type,
                "bbox": bbox,
                "contextualized_content": ctx,
                "chunk_strategy": self.strategy,
            }
            validate_chunk_record(rec, where="chunk")
            out.append(rec)
        return out


def _run(doc: Document, strategy: str, opts: dict) -> list[dict]:
    ch = Chunker(doc, strategy, opts)
    return ch.records({"adaptive": ch.adaptive, "page": ch.pages, "fixed": ch.fixed}[strategy]())


def adaptive(doc: Document, **opts) -> list[dict]:
    """Structure-tree chunking: the largest semantic unit that fits ``max_tokens`` becomes one chunk."""
    return _run(doc, "adaptive", opts)


def page(doc: Document, **opts) -> list[dict]:
    """One chunk per page/slide/sheet; a page over ``max_tokens`` is split with the adaptive rules."""
    return _run(doc, "page", opts)


def fixed(doc: Document, **opts) -> list[dict]:
    """Structure-blind fixed token windows with ``overlap`` (baseline)."""
    return _run(doc, "fixed", opts)
