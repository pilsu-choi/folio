"""Fact-recall judge for figure descriptions. Judge served on :8010 (gemma-4-12B-it; different family from all candidates).
usage: judge.py <outdir> <judge-model>  -> <outdir>/figure_scores.json"""
import glob, json, os, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
S = os.path.expanduser("~/folio-m1-bench/samples")
out, jm = os.path.expanduser(sys.argv[1]), sys.argv[2]
P = ("You are a strict grader. Below is a Korean description of a chart/diagram, and one reference fact.\n"
     "Decide whether the description states this fact. Numbers, units, labels and direction (increase/decrease) must be "
     "consistent with the fact; paraphrase is fine; a partly wrong or missing number means NO. "
     "Answer with exactly one word: YES or NO.\n\n[Description]\n{d}\n\n[Fact]\n{f}\n\nAnswer:")
def ask(d, f):
    body = {"model": jm, "max_tokens": 8, "temperature": 0, "messages": [{"role": "user", "content": P.format(d=d[:6000], f=f)}]}
    r = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:8010/v1/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=300))
    return r["choices"][0]["message"]["content"].strip().upper().startswith("YES")
items = []
for f in sorted(glob.glob(f"{out}/*/figure__*.json") + glob.glob(f"{out}/*/fignative__*.json")):
    d = json.load(open(f)); facts = json.load(open(f"{S}/truth/{d['id']}.figure.json"))["facts"]
    items.append((os.path.basename(os.path.dirname(f)), d["task"], d["id"], d["text"], facts))
def run(it):
    m, t, i, txt, facts = it
    ys = [ask(txt, f) for f in facts] if txt.strip() else [False] * len(facts)
    return dict(model=m, task=t, id=i, hits=ys, recall=sum(ys) / len(ys), facts=facts)
with ThreadPoolExecutor(6) as ex: res = list(ex.map(run, items))
json.dump(res, open(f"{out}/figure_scores.json", "w"), ensure_ascii=False, indent=1)
from collections import defaultdict
agg = defaultdict(list)
for r in res: agg[(r["model"], r["task"])].append(r["recall"])
for k, v in sorted(agg.items()): print(k, len(v), "recall %.3f" % (sum(v) / len(v)))
