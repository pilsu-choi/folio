"""Per-group span recall@2k (and @1k) for candidate configs.  usage: breakdown.py scores.json testset.jsonl cfg1,cfg2,..."""
import json, sys
import numpy as np
res = json.load(open(sys.argv[1])); qs = [json.loads(l) for l in open(sys.argv[2])]; cfgs = sys.argv[3].split(",")
G = {"all": lambda q: True, "long (d01,d09,d10,d11)": lambda q: q["doc_id"][:3] in ("d01", "d09", "d10", "d11"),
     "terms 조/항 (d12-d16)": lambda q: q["doc_id"][:3] in ("d12", "d13", "d14", "d15", "d16"),
     "table-heavy (d05,d06,d08,d11)": lambda q: q["doc_id"][:3] in ("d05", "d06", "d08", "d11"),
     "type=fact": lambda q: q["type"] == "fact", "type=table": lambda q: q["type"] == "table", "type=section": lambda q: q["type"] == "section"}
print("| group (n) | " + " | ".join(cfgs) + " |"); print("|---|" + "---|" * len(cfgs))
for g, f in G.items():
    idx = [i for i, q in enumerate(qs) if f(q)]
    print(f"| {g} ({len(idx)}) | " + " | ".join(f"{np.mean([res[c]['per_query'][i]['rec2000'] for i in idx]):.3f} / {np.mean([res[c]['per_query'][i]['rec1000'] for i in idx]):.3f}" for c in cfgs) + " |")
