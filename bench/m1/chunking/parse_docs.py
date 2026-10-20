"""Parse the 16 digital docs with folio's text-layer path (heuristic backend) -> Document JSON."""
import json, sys, time, os
ROOT = os.path.expanduser("~/folio-m1-bench")
sys.path.insert(0, ROOT + "/folio-dev")
import folio.parse as P
BACKEND = sys.argv[1] if len(sys.argv) > 1 else "heuristic"
if BACKEND == "docling": BACKEND = {"fn": "docling", "device": "cuda"}  # GPU: run under flock
if BACKEND == "docling_cpu": BACKEND = {"fn": "docling", "device": "cpu"}
ONLY = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
OUT = ROOT + "/out/chunking/docs_" + (sys.argv[1] if len(sys.argv) > 1 else "heuristic"); os.makedirs(OUT, exist_ok=True)
for d in json.load(open(ROOT + "/samples/manifest.json")):
    if d["kind"] != "digital" or (ONLY and d["id"][:3] not in ONLY): continue
    t = time.time()
    doc = P.default("file://" + ROOT + "/samples/" + d["file"], backend=BACKEND, vlm=False, ocr_fallback=False)
    json.dump(doc.model_dump() if hasattr(doc, "model_dump") else dict(doc), open(f"{OUT}/{d['id']}.json", "w"), ensure_ascii=False)
    nb = sum(len(p.blocks) for p in doc.pages); nh = sum(1 for p in doc.pages for b in p.blocks if b.type == "heading")
    print(d["id"], len(doc.pages), nb, nh, doc.meta["backend"], round(time.time() - t, 1), flush=True)
