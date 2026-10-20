import json, os, sys, re
ROOT = os.path.expanduser("~/folio-m1-bench")
sys.path.insert(0, ROOT + "/folio-dev"); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from folio.contracts import Document
from folio.chunk.structure import build_leaves

OUT = ROOT + "/out/chunking"


def load_docs(parse_dir):
    docs = {}
    for f in sorted(os.listdir(parse_dir)):
        if f.endswith(".json"):
            j = json.load(open(f"{parse_dir}/{f}"))
            for p in j["pages"]:  # docling emits a few degenerate 39-139 column tables (d01 p31, p33); drop them (see RESULTS.md)
                p["blocks"] = [b for b in p["blocks"] if not (b["type"] == "table" and max((len(r) for r in b.get("cells") or []), default=0) > 30)]
            docs[f[:-5]] = Document.model_validate(j)
    return docs


def page_texts(doc):
    """{page: text}: the config-independent 'source page text' = leaf texts (what the chunker sees) joined by blank lines."""
    out = {}
    for lf in build_leaves(doc, False):
        out.setdefault(lf.page, []).append(lf.text)
    return {p: "\n\n".join(t) for p, t in out.items()}


def norm_map(s):
    """Whitespace-collapsed text (also no spaces next to '|') and an index map back to original offsets."""
    chars, idx, n, i = [], [], len(s), 0
    while i < n:
        c = s[i]
        if c.isspace():
            j = i
            while j < n and s[j].isspace(): j += 1
            if chars and chars[-1] != "|" and j < n and s[j] != "|":
                chars.append(" "); idx.append(i)
            i = j
        else:
            chars.append(c); idx.append(i); i += 1
    return "".join(chars), idx


def page_headers(doc):
    """{page: [normalised table header text]}: repeated in every split chunk of a table, so useless as evidence."""
    out = {}
    for lf in build_leaves(doc, False):
        if lf.kind == "table" and lf.header:
            out.setdefault(lf.page, []).append(norm_map(lf.header)[0])
    return out
