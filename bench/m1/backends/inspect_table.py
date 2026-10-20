import sys, json
import paddle  # before fitz
sys.path.insert(0, "/home/pilsu/folio-m1-bench/folio-dev"); sys.path.insert(0, "/home/pilsu/folio-m1-bench/folio-dev/scripts")
from folio.backends import resolve_backend
from folio.parse import default
from bench_backends import table_scores
m = {e["id"]: e for e in json.load(open("samples/manifest.json"))["docs" if isinstance(json.load(open("samples/manifest.json")), dict) else 0]} if False else None
man = json.load(open("samples/manifest.json")); man = man["docs"] if isinstance(man, dict) else man
e = [x for x in man if x["id"].startswith(sys.argv[1])][0]
pdf = [f for f in [e["file"]] ][0]
truth = json.load(open([t for t in e["truth"] if t.endswith(".table.json")][0] if e["truth"][0].startswith("samples") else "samples/"+[t for t in e["truth"] if t.endswith(".table.json")][0]))
doc = default(pdf if pdf.startswith("samples") else "samples/"+pdf, backend=resolve_backend({"fn": "ppstructure", "device": "cpu", "cpu_threads": 8}))
print("SCORE", table_scores(doc, truth))
print("TRUTH rows", truth["rows"], "cols", truth["cols"]); [print("  ", r) for r in truth["cells"][:6]]
for p in doc.pages if hasattr(doc,"pages") else []:
    pass
for b in (doc.blocks if hasattr(doc, "blocks") else []):
    if b.type == "table":
        cells = b.table.cells if hasattr(b,"table") else b.cells
        print("GOT", type(cells)); print(str(cells)[:1500])
tabs = [b for _, b in doc.iter_blocks() if b.type == "table"]
t = max(tabs, key=lambda b: len(b.cells) * len(b.cells[0]))
print("GOT rows", len(t.cells), "cols", len(t.cells[0]), "spans", [s.model_dump() for s in (t.spans or [])])
for r in t.cells: print("  ", [c[:60] for c in r])
print("TRUTH spans", truth["spans"])
