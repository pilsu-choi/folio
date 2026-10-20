import json
m=json.load(open("../../samples/manifest.json"))
done={l.split()[0] for l in open("ppstructure_cpu_partial.log")}
rest=[(e["id"],e["pages"]) for e in m if e["kind"] in("digital","scan","table") and e["id"] not in done]
sh=[[0,[]] for _ in range(4)]
for i,p in sorted(rest,key=lambda x:-x[1]):
    s=min(sh,key=lambda x:x[0]); s[0]+=p; s[1].append(i)
for n,(w,ids) in enumerate(sh): open(f"shard{n}.ids","w").write(",".join(ids)); print(n,w,ids)
