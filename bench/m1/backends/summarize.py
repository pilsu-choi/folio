#!/usr/bin/env python
"""Aggregate run_manifest.py outputs into markdown tables. usage: summarize.py out/backends/*.json"""
import json, statistics as st, sys

def q95(v): v = sorted(v); return v[min(len(v) - 1, int(round(0.95 * (len(v) - 1))))] if v else float("nan")
def mean(v): return sum(v) / len(v) if v else float("nan")

for f in sys.argv[1:]:
    d = json.load(open(f)); R = d["rows"]
    print(f"\n### {d['backend']} / {d['device']}  (warmup {d['warmup_sec']:.0f}s excluded; mem {d['mem']})")
    print("| kind | files | pages | fail | sec/page median | sec/page p95 | sec/page mean | metrics |\n|---|---|---|---|---|---|---|---|")
    for k in ("digital", "scan", "table"):
        rs = [r for r in R if r["kind"] == k]; ok = [r for r in rs if "failed" not in r]
        pg = [m / 1000 for r in ok for m in r["pages_ms"]]
        if k == "digital": m = f"text_match mean {mean([r['text_match'] for r in ok]):.4f} min {min([r['text_match'] for r in ok], default=float('nan')):.4f}"
        elif k == "scan":
            o = [r["ocr_similarity"] for r in ok if "ocr_similarity" in r]; m = f"OCR sim mean {mean(o):.3f} (n={len(o)}) min {min(o, default=float('nan')):.3f}"
        else:
            t = [r for r in ok]; m = (f"found {sum(r['found'] for r in t)}/{len(rs)} rows_ok {sum(r['rows_ok'] for r in t)} cols_ok {sum(r['cols_ok'] for r in t)} "
                                     f"spans_ok {sum(r['spans_ok'] for r in t)} cell_acc mean {mean([r['cell_acc'] for r in t]):.3f}")
        print(f"| {k} | {len(rs)} | {len(pg)} | {len(rs)-len(ok)} | {st.median(pg) if pg else float('nan'):.2f} | {q95(pg):.2f} | {mean(pg):.2f} | {m} |")
    errs = [(r['id'], r.get('failed') or r['errors']) for r in R if r.get('failed') or r.get('errors')]
    print("page-level errors / failures:", errs or "none")
