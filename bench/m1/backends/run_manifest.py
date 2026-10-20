#!/usr/bin/env python
"""Thin adapter: run folio's StructureBackends over samples/manifest.json, reusing
scripts/bench_backends.py metrics (text_match, table_scores) and its OCR similarity formula.

  python run_manifest.py --backend docling --device cuda --samples ~/folio-m1-bench/samples --out OUT.json
Run once per backend per process; each page/file is parsed with folio.parse.default (same path as the harness).
Peak memory: process RSS high-water mark (ru_maxrss) + GPU/unified-memory delta sampled via nvidia-smi is N/A on GB10,
so torch/paddle allocator stats are added when available.
"""
from __future__ import annotations

import argparse, difflib, json, os, resource, sys, time, traceback
from pathlib import Path

try:
    import paddle  # noqa: F401  (must load before pymupdf on aarch64, else segfault)
except ImportError:
    pass
ROOT = Path(os.environ.get("FOLIO_ROOT", Path.home() / "folio-m1-bench/folio-src"))  # folio-dev for the post-fix rerun
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from bench_backends import norm, table_scores, text_match  # noqa: E402
from folio.parse import default  # noqa: E402


def entries(manifest):
    items = manifest if isinstance(manifest, list) else manifest.get("samples") or manifest.get("items") or list(manifest.values())
    for e in items:
        yield e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True, choices=["docling", "ppstructure"])
    ap.add_argument("--device", required=True)
    ap.add_argument("--samples", default=str(Path.home() / "folio-m1-bench/samples"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--ids", default="", help="comma list of sample ids (quiet-machine re-timing subset)")
    ap.add_argument("--cpu-threads", type=int, default=0, help="ppstructure only; 0 = paddle default")
    ap.add_argument("--kinds", default="digital,scan,table")
    a = ap.parse_args()
    S = Path(a.samples)
    man = json.loads((S / "manifest.json").read_text())
    spec = {"fn": a.backend, "device": a.device}
    if a.cpu_threads and a.backend == "ppstructure":
        spec["cpu_threads"] = a.cpu_threads
    from folio.backends import resolve_backend
    backend = resolve_backend(spec)
    import pymupdf
    from folio.backends.base import Word
    probe = pymupdf.open(S / "pdf" / "d02_fsc_virtual_asset.pdf")[0].get_pixmap(dpi=144).tobytes("png")
    t = time.perf_counter()  # warmup: model download/load and both docling converters (layout-only + OCR), excluded from timings
    backend.analyze(probe, [])
    backend.analyze(probe, None)
    warm = time.perf_counter() - t
    print("warmup_sec", warm, flush=True)
    rows = []
    kinds = a.kinds.split(",")
    for e in entries(man):
        kind = e.get("kind")
        if kind not in kinds or (a.ids and e["id"] not in a.ids.split(",")):
            continue
        sid = e["id"]
        path = S / (e.get("path") or e.get("file"))
        r = {"id": sid, "kind": kind, "path": str(path)}
        try:
            t = time.perf_counter()
            doc = default(str(path), backend=backend)
            r["sec"] = time.perf_counter() - t
            r["pages"] = len(doc.pages)
            r["pages_ms"] = doc.meta["timings"]["pages_ms"]
            r["errors"] = [str(x)[:200] for x in doc.meta["errors"]]
            if kind == "digital":
                r["text_match"] = text_match(doc, path)
            elif kind == "scan":
                got = norm(" ".join(b.text or "" for _, b in doc.iter_blocks()))
                r["chars_out"] = len(got)
                tp = S / "truth" / f"{sid}.truth.json"
                if tp.exists():  # r* genuine scans have no text truth: timing/failure only
                    truth = json.loads(tp.read_text())["lines"]
                    r["ocr_similarity"] = difflib.SequenceMatcher(None, norm(" ".join(truth)), got).ratio()
            elif kind == "table":
                truth = json.loads((S / "truth" / f"{sid}.table.json").read_text())
                r.update(table_scores(doc, truth))
        except Exception as ex:
            r["failed"] = f"{type(ex).__name__}: {ex}"[:300]
            r["tb"] = traceback.format_exc()[-600:]
        rows.append(r)
        print(sid, kind, {k: v for k, v in r.items() if k in ("sec", "pages", "failed", "text_match", "ocr_similarity", "cell_acc")}, flush=True)
    mem = {"rss_peak_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    try:
        import torch
        if torch.cuda.is_available():
            mem["torch_peak_alloc_mb"] = torch.cuda.max_memory_allocated() / 2**20
    except ImportError:
        pass
    try:
        import paddle
        if paddle.device.is_compiled_with_cuda():
            mem["paddle_peak_alloc_mb"] = paddle.device.cuda.max_memory_allocated() / 2**20
    except Exception:
        pass
    Path(a.out).write_text(json.dumps({"backend": a.backend, "warmup_sec": warm, "device": a.device, "mem": mem, "rows": rows},
                                      ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
