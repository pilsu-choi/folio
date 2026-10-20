#!/usr/bin/env python
"""Summarize off / ocr_first / page runs and simulate ocr_first thresholds.

  python summarize.py OUT_DIR [PAGE_DIR]   (reads off.json, ocr_first.json, page.json; page.json from PAGE_DIR if given)

Threshold t: a page keeps its ocr_first result when its OCR confidence >= t, else it takes the page-VLM
result; its time is the OCR time plus the page-VLM time (what ocr_first does at that threshold).
"""
import json
import statistics as st
import sys
from pathlib import Path

D = Path(sys.argv[1])
P = Path(sys.argv[2]) if len(sys.argv) > 2 else D
runs = {p: {r["id"]: r for r in json.loads(((P if p == "page" else D) / f"{p}.json").read_text())["rows"]}
        for p in ("off", "ocr_first", "page")}
ids = [i for i in runs["page"] if all(i in runs[p] and "failed" not in runs[p][i] for p in runs)]


def score(r):
    return r.get("ocr_similarity", r.get("cell_acc"))


def line(name, pick):
    rows = [pick(i) for i in ids]
    scan = [score(r) for i, (r, _) in zip(ids, rows) if runs["page"][i]["kind"] == "scan"]
    tab = [score(r) for i, (r, _) in zip(ids, rows) if runs["page"][i]["kind"] == "table"]
    nofig = [r.get("ocr_similarity_nofig") for i, (r, _) in zip(ids, rows) if runs["page"][i]["kind"] == "scan"]
    nofig = f"{st.mean(nofig):.3f}" if all(x is not None for x in nofig) else "-"
    secs = [s for _, s in rows]
    bbox = st.mean(r["page_bbox_frac"] for r, _ in rows)
    vlmpages = sum(r.get("sources", {}).keys() == {"vlm"} and r["page_bbox_frac"] == 1 for r, _ in rows)
    print(f"| {name} | {st.mean(scan):.3f} | {nofig} | {st.mean(tab):.3f} | {st.median(secs):.1f} | {sum(secs) / len(secs):.1f} "
          f"| {1 - bbox:.2f} | {vlmpages}/{len(rows)} |")


print("| policy | scan OCR sim (n=%d) | scan sim w/o figure blocks | table cell_acc (n=%d) | median s/page | mean s/page | blocks with real bbox | whole-page VLM |"
      % (sum(runs["page"][i]["kind"] == "scan" for i in ids), sum(runs["page"][i]["kind"] == "table" for i in ids)))
print("| --- | --- | --- | --- | --- | --- | --- | --- |")
line("off (OCR only)", lambda i: (runs["off"][i], runs["off"][i]["sec"]))
line("page (current)", lambda i: (runs["page"][i], runs["page"][i]["sec"]))
for t in (0.0, 0.80, 0.85, 0.90, 0.93, 0.95, 0.97):
    def pick(i, t=t):
        o = runs["ocr_first"][i]
        if (o.get("ocr_conf") or 0) >= t:
            return o, o["sec"]
        return runs["page"][i], runs["off"][i]["sec"] + runs["page"][i]["sec"]
    line(f"ocr_first t={t:.2f}", pick)
print()
print("| id | kind | ocr_conf | off | ocr_first | page |")
print("| --- | --- | --- | --- | --- | --- |")
for i in ids:
    c = runs["ocr_first"][i].get("ocr_conf")
    print(f"| {i} | {runs['page'][i]['kind']} | {c if c is None else round(c, 3)} | "
          + " | ".join(f"{score(runs[p][i]):.3f}" for p in ("off", "ocr_first", "page")) + " |")
bad = [(p, i, r.get("failed")) for p in runs for i, r in runs[p].items() if "failed" in r]
if bad:
    print("\nfailed:", bad)
