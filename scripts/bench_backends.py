#!/usr/bin/env python
"""Benchmark StructureBackends on the parse fixtures (FR-19).

  python scripts/bench_backends.py --backends heuristic,docling,ppstructure --fixtures tests/fixtures --out bench-results/

Per backend: block-text match rate vs PyMuPDF text (digital pages), OCR text similarity vs ground truth
(scanned pages), table rows/cols/cell-text accuracy vs `table.truth.json`, sec/page, schema validation pass.
Backends whose optional dependencies are missing are reported as "skipped: <reason>".
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import jsonschema
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from folio.backends import resolve_backend
from folio.parse import default

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "folio/contracts/schemas/document.schema.json").read_text())
DIGITAL = ["digital.pdf", "contract_ko.pdf", "rotated.pdf"]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def text_match(doc, pdf: Path) -> float:
    """Multiset word recall of PyMuPDF text within block texts."""
    src = pymupdf.open(pdf)
    hit = tot = 0
    for page, pg in zip(doc.pages, src):
        want = Counter(norm(pg.get_text()).split())
        got = Counter(norm(" ".join(b.text or "" for b in page.blocks)).split())
        tot += sum(want.values())
        hit += sum((want & got).values())
    return hit / tot if tot else 1.0


def table_scores(doc, truth: dict) -> dict:
    tabs = [b for _, b in doc.iter_blocks() if b.type == "table"]
    if not tabs:
        return {"found": False, "rows_ok": False, "cols_ok": False, "cell_acc": 0.0, "spans_ok": False}
    t = max(tabs, key=lambda b: len(b.cells) * len(b.cells[0]))
    rows, cols = len(t.cells), len(t.cells[0])
    ok = tot = 0
    for r, row in enumerate(truth["cells"]):
        for c, want in enumerate(row):
            tot += 1
            ok += r < rows and c < cols and norm(t.cells[r][c]) == norm(want)
    spans = [s.model_dump() for s in (t.spans or [])]
    return {"found": True, "rows_ok": rows == truth["rows"], "cols_ok": cols == truth["cols"],
            "cell_acc": ok / tot, "spans_ok": spans == truth["spans"]}


def run_backend(name: str, fixtures: Path) -> dict:
    try:
        backend = resolve_backend(name)
        # force lazy imports / model construction to surface missing deps early
        from folio.backends.base import Word  # noqa: F401
        probe = pymupdf.open(fixtures / "digital.pdf")[0].get_pixmap(dpi=72).tobytes("png")
        backend.analyze(probe, [])
    except ImportError as exc:
        return {"status": f"skipped: {exc}"}
    except Exception as exc:  # model/runtime failure is reported, not fatal
        return {"status": f"failed: {type(exc).__name__}: {exc}"}
    res: dict = {"status": "ok", "digital_match": [], "schema_ok": 0, "schema_total": 0, "pages": 0, "sec": 0.0,
                 "errors": []}

    def parse(f: str):
        t = time.perf_counter()
        doc = default(str(fixtures / f), backend=backend, vlm=False)
        res["sec"] += time.perf_counter() - t
        res["pages"] += len(doc.pages)
        res["schema_total"] += 1
        try:
            jsonschema.validate(json.loads(doc.model_dump_json(exclude_none=True)), SCHEMA)
            res["schema_ok"] += 1
        except jsonschema.ValidationError as exc:
            res["errors"].append(f"{f}: schema {exc.message[:80]}")
        res["errors"] += [f"{f}: p{e['page']} {e['error'][:100]}" for e in doc.meta["errors"]]
        return doc

    for f in DIGITAL:
        if (fixtures / f).exists():
            res["digital_match"].append(text_match(parse(f), fixtures / f))
    if (fixtures / "table.pdf").exists():
        res["table"] = table_scores(parse("table.pdf"), json.loads((fixtures / "table.truth.json").read_text()))
    if (fixtures / "scanned.pdf").exists():
        truth = json.loads((fixtures / "scanned.truth.json").read_text())["lines"]
        doc = parse("scanned.pdf")
        got = norm(" ".join(b.text or "" for _, b in doc.iter_blocks()))
        res["ocr_similarity"] = difflib.SequenceMatcher(None, norm(" ".join(truth)), got).ratio()
        res["ocr_calls"] = doc.meta["calls"]["ocr"]
    res["sec_per_page"] = res["sec"] / max(res["pages"], 1)
    return res


def markdown(results: dict) -> str:
    lines = ["| backend | status | digital text match | table rows/cols | cell acc | spans | OCR similarity | sec/page | schema |",
             "|---|---|---|---|---|---|---|---|---|"]
    for n, r in results.items():
        if r["status"] != "ok":
            lines.append(f"| {n} | {r['status'][:150]} | | | | | | | |")
            continue
        t = r.get("table", {})
        dm = sum(r["digital_match"]) / len(r["digital_match"]) if r["digital_match"] else float("nan")
        lines.append(f"| {n} | ok | {dm:.3f} | {'Y' if t.get('rows_ok') else 'N'}/{'Y' if t.get('cols_ok') else 'N'} | "
                     f"{t.get('cell_acc', 0):.2f} | {'Y' if t.get('spans_ok') else 'N'} | {r.get('ocr_similarity', 0):.2f} | "
                     f"{r['sec_per_page']:.2f} | {r['schema_ok']}/{r['schema_total']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backends", default="heuristic")
    ap.add_argument("--fixtures", default=str(ROOT / "tests/fixtures"))
    ap.add_argument("--out", default="bench-results")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results = {n: run_backend(n, Path(a.fixtures)) for n in a.backends.split(",")}
    (out / "bench.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    md = markdown(results)
    (out / "bench.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
