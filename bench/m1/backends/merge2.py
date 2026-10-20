import json,glob
old=json.load(open("ppstructure_cpu.json")); new=[]
for f in sorted(glob.glob("ppstructure_fixed_shard?.json")): new+=json.load(open(f))["rows"]
ids={r["id"] for r in new}
rows=new+[dict(r,note="carried over from pre-fix run (not rerun)") for r in old["rows"] if r["id"] not in ids]
json.dump(dict(backend="ppstructure",device="cpu",fixed=True,warmup_sec=0,mem={},rows=rows),open("ppstructure_cpu_fixed.json","w"),ensure_ascii=False,indent=1)
o={r["id"]:r for r in old["rows"]}
for r in sorted(new,key=lambda r:r["id"]):
    b=o[r["id"]]; k=r["kind"]
    if k=="table": print(r["id"],"cell_acc",round(b["cell_acc"],2),"->",round(r["cell_acc"],2),"rows",b["rows_ok"],r["rows_ok"],"cols",b["cols_ok"],r["cols_ok"],"spans",b["spans_ok"],r["spans_ok"],"err",len(r["errors"]),"fail",r.get("failed"))
    elif k=="scan": print(r["id"],"ocr",round(b.get("ocr_similarity",-1),3),"->",round(r.get("ocr_similarity",-1),3),"chars",b.get("chars_out"),r.get("chars_out"),"err",len(r["errors"]))
    else: print(r["id"],"errpages",len(b["errors"]),"->",len(r["errors"]),"tm",round(r["text_match"],4),r.get("failed"))
