"""Score the M1 LLM testset (249 Korean queries) with folio.eval on several chunk configs (lexical retriever).
usage: PYTHONPATH=. python bench/eval/m1_span_check.py <dir containing m1docs/> bench/m1/chunking/testset.jsonl"""
import json, math, sys, time
from collections import Counter
from pathlib import Path
from folio import eval as ev
from folio.chunk.strategies import adaptive, fixed
from folio.contracts import Document
from folio.eval.testset import norm_map, page_texts
from folio.retrieval.base import Hit

S = Path(sys.argv[1]); TS = sys.argv[2]  # S/m1docs = M1 parse_final Document JSONs (pgx ~/folio-m1-bench/out/chunking/parse_final)
docs = []
for f in sorted((S / "m1docs").glob("*.json")):
    j = json.loads(f.read_text())
    for p in j["pages"]:  # same degenerate-table filter as bench/m1/chunking/common.py
        p["blocks"] = [b for b in p["blocks"] if not (b["type"] == "table" and max((len(r) for r in b.get("cells") or []), default=0) > 30)]
    j["doc_id"] = f.stem
    docs.append(Document.model_validate(j))
pt = {(d.doc_id, p): t for d in docs for p, t in page_texts(d).items()}
items = []
for l in open(TS):
    r = json.loads(l)
    items.append({"qid": r["qid"], "type": r["type"], "query": r["question"],
                  "evidence": [{"doc_id": r["doc_id"], "page": r["page"], "text": t} for t in r["span_text"]]})
located = sum(all(norm_map(e["text"].strip())[0] in norm_map(pt[(e["doc_id"], e["page"])])[0] for e in it["evidence"]) for it in items)
print(json.dumps({"items": len(items), "spans_located_in_page_texts": located}))

def grams(s): s = "".join(s.split()); return Counter(s[i:i+2] for i in range(len(s) - 1))
class Lex:
    def __init__(self, chunks):
        self.c = chunks; self.g = [grams(c["content"]) for c in chunks]
        df = Counter(k for g in self.g for k in g); n = len(chunks)
        self.idf = {k: math.log(1 + n / v) for k, v in df.items()}
    def search(self, query, k=10, **_):
        q = grams(query)
        sc = [sum(self.idf.get(t, 0) * min(v, g.get(t, 0)) for t, v in q.items()) / (1 + 0.002 * len(c["content"]))
              for c, g in zip(self.c, self.g)]
        top = sorted(range(len(sc)), key=lambda i: -sc[i])[:k]
        return [Hit(self.c[i]["chunk_id"], self.c[i]["doc_id"], sc[i], self.c[i]["content"], self.c[i]) for i in top]

configs = {"adaptive_1024_128": lambda d: adaptive(d, max_tokens=1024, min_tokens=128),
           "adaptive_512_64": lambda d: adaptive(d, max_tokens=512, min_tokens=64),
           "adaptive_256_64": lambda d: adaptive(d, max_tokens=256, min_tokens=64),
           "fixed_512": lambda d: fixed(d, max_tokens=512)}
res = {}
for name, fn in configs.items():
    t = time.time(); chunks = [c for d in docs for c in fn(d)]
    r = ev.run(items, Lex(chunks), ks=(1, 5, 10), chunks=chunks); res[name] = r
    print(json.dumps({"config": name, "chunks": len(chunks), "unreachable": len(r["unreachable"]), "sec": round(time.time() - t, 1),
                      "metrics": {k: round(v, 3) for k, v in r["metrics"].items() if k in ("recall@5", "all@5", "mrr", "ndcg@10")},
                      "by_type_recall@5": {ty: (v["n"], round(v["metrics"]["recall@5"], 3)) for ty, v in r["by_type"].items()}}, ensure_ascii=False))
c = ev.compare(res["adaptive_1024_128"], res["adaptive_512_64"])
print(json.dumps({"compare": "1024_128 -> 512_64", "n": c["n"], **{m: {k: (round(v, 3) if isinstance(v, float) else [round(x, 3) for x in v]) for k, v in c["metrics"][m].items()} for m in ("recall@5", "all@5", "mrr")}}, ensure_ascii=False))
