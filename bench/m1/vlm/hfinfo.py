import sys,json,urllib.request
for m in sys.argv[1:]:
    d=json.load(urllib.request.urlopen(f"https://huggingface.co/api/models/{m}?blobs=true"))
    c=d.get("cardData") or {}
    w=sum(s.get("size") or 0 for s in d["siblings"] if s["rfilename"].endswith((".safetensors",".bin")))
    print(m,"| license:",c.get("license"),c.get("license_name"),"| gated:",d.get("gated"),"|",round(w/1e9,2),"GB")
