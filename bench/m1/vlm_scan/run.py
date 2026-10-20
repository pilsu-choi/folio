#!/usr/bin/env python
"""vlm_scan policy bench: synthetic scans s01-s10 (text truth) + table images t01-t10 (cell truth) through
folio.parse.default with PP-StructureV3 (CPU) as OCR backend and a VLM server.

  python run.py --policy page|ocr_first|off --vlm-url http://localhost:8010/v1 --model Qwen3.5-9B --out OUT.json

`ocr_first` runs with ocr_min_conf=0 (never falls back to the VLM page) and records the OCR confidence per
page, so any threshold can be simulated afterwards from the `page` and `ocr_first` runs (summarize.py).
"""
from __future__ import annotations

import argparse, collections, difflib, json, os, resource, sys, time
from pathlib import Path

try:
    import paddle  # noqa: F401  (must load before pymupdf on aarch64)
except ImportError:
    pass
ROOT = Path(os.environ.get("FOLIO_ROOT", Path.home() / "folio-m1-bench/folio-scan"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from bench_backends import norm, table_scores  # noqa: E402
from folio.backends import resolve_backend  # noqa: E402
from folio.parse import default  # noqa: E402


class Spy:
    """Wraps the backend and keeps the char-weighted OCR confidence of the last OCR call."""

    def __init__(self, inner):
        self.inner, self.conf = inner, None

    def analyze(self, png, words):
        s = self.inner.analyze(png, words)
        if words is None:
            sc = [(len(w.text), w.conf) for w in s.ocr_words or [] if w.text.strip() and w.conf is not None]
            self.conf = sum(k * c for k, c in sc) / sum(k for k, _ in sc) if sc else None
        return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True, choices=["page", "ocr_first", "off"])
    ap.add_argument("--vlm-url", default="http://localhost:8010/v1")
    ap.add_argument("--model", default="Qwen3.5-9B")
    ap.add_argument("--samples", default=str(Path.home() / "folio-m1-bench/samples"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--ids", default="")
    ap.add_argument("--save", default="", help="dir to write each Document JSON (debugging)")
    a = ap.parse_args()
    S = Path(a.samples)
    man = json.loads((S / "manifest.json").read_text())
    spy = Spy(resolve_backend({"fn": "ppstructure", "device": "cpu"}))
    spy.inner.warmup()
    vlm = False if a.policy == "off" else {"url": a.vlm_url, "model": a.model, "timeout": 900, "max_tokens": 8192}
    opts = {"vlm": vlm, "vlm_scan": "page" if a.policy == "off" else a.policy, "ocr_min_conf": 0.0}
    rows = []
    for e in man:
        sid, kind = e["id"], e["kind"]
        if a.ids and sid not in a.ids.split(","):
            continue
        if kind == "scan" and (S / "truth" / f"{sid}.truth.json").exists():
            path = S / e["file"]
        elif kind == "table":
            path = S / e["file"].replace(".pdf", ".png")
        else:
            continue
        spy.conf = None
        r = {"id": sid, "kind": kind}
        try:
            t = time.perf_counter()
            doc = default(str(path), backend=spy, **opts)
            r["sec"] = time.perf_counter() - t
            if a.save:
                Path(a.save).mkdir(parents=True, exist_ok=True)
                (Path(a.save) / f"{sid}.{a.policy}.json").write_text(doc.model_dump_json(indent=1))
            r["calls"], r["fallbacks"], r["errors"] = doc.meta["calls"], doc.meta["vlm_fallbacks"], doc.meta["errors"]
            r["ocr_conf"] = spy.conf
            blocks = [b for _, b in doc.iter_blocks()]
            pg = doc.pages[0]
            full = [0.0, 0.0, pg.width, pg.height]
            r["sources"] = dict(collections.Counter(b.text_source for b in blocks))
            r["page_bbox_frac"] = sum(b.bbox == full for b in blocks) / max(len(blocks), 1)
            if kind == "scan":
                truth = json.loads((S / "truth" / f"{sid}.truth.json").read_text())["lines"]
                got = norm(" ".join(b.text or "" for b in blocks))
                r["ocr_similarity"] = difflib.SequenceMatcher(None, norm(" ".join(truth)), got).ratio()
                nofig = norm(" ".join(b.text or "" for b in blocks if b.type != "figure"))
                r["ocr_similarity_nofig"] = difflib.SequenceMatcher(None, norm(" ".join(truth)), nofig).ratio()
            else:
                r.update(table_scores(doc, json.loads((S / "truth" / f"{sid}.table.json").read_text())))
        except Exception as ex:
            r["failed"] = f"{type(ex).__name__}: {ex}"[:300]
        rows.append(r)
        print(sid, {k: r.get(k) for k in ("sec", "ocr_conf", "ocr_similarity", "cell_acc", "sources", "failed")}, flush=True)
    Path(a.out).write_text(json.dumps({"policy": a.policy, "model": a.model, "rows": rows,
                                       "rss_peak_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024},
                                      ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
