import json,ast,re,glob
rows=[]
for l in open("ppstructure_cpu_partial.log"):
    i,k,rest=l.split(" ",2); d=ast.literal_eval(rest.strip())
    rows.append(dict(id=i,kind=k,sec=d["sec"],pages=d["pages"],text_match=d["text_match"],pages_ms=[d["sec"]/d["pages"]*1000]*d["pages"],errors=[],note="from log of interrupted run"))
mem=[]
for f in sorted(glob.glob("ppstructure_cpu_shard?.json")):
    d=json.load(open(f)); rows+=d["rows"]; mem.append(d["mem"]); w=d["warmup_sec"]
json.dump(dict(backend="ppstructure",device="cpu",warmup_sec=w,mem={"shard_rss_peak_mb":[m["rss_peak_mb"] for m in mem]},rows=rows),open("ppstructure_cpu.json","w"),ensure_ascii=False,indent=1)
print(len(rows))
