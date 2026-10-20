"""Crawl FSC (금융위원회) press-release list pages and download PDF attachments to a staging dir.
usage: fetch_fsc.py OUTDIR [pages]   -> writes OUTDIR/fsc_<postNo>_<fileNo>.pdf and OUTDIR/fsc_index.json"""
import re, sys, json, os, html, requests
out = sys.argv[1]; npages = int(sys.argv[2]) if len(sys.argv) > 2 else 6
os.makedirs(out, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
B = "https://www.fsc.go.kr"
idx = []
posts = []
for p in range(1, npages + 1):
    t = S.get(f"{B}/no010101?curPage={p}", timeout=30).text
    posts += re.findall(r'href="(/no010101/(\d+)[^"]*)"[^>]*>\s*([^<]+?)\s*<', t)
seen = set()
for href, no, title in posts:
    if no in seen: continue
    seen.add(no)
    t = S.get(f"{B}/no010101/{no}", timeout=30).text
    for fn in sorted(set(re.findall(r"upperNo=%s&amp;fileTy=ATTACH&amp;fileNo=(\d+)" % no, t))):
        r = S.get(f"{B}/comm/getFile", params=dict(srvcId="BBSTY1", upperNo=no, fileTy="ATTACH", fileNo=fn), timeout=60)
        if r.content[:4] == b"%PDF":
            f = f"{out}/fsc_{no}_{fn}.pdf"; open(f, "wb").write(r.content)
            idx.append(dict(file=f, post=no, title=html.unescape(title), url=f"{B}/no010101/{no}", size=len(r.content)))
            print(f, len(r.content), title, flush=True)
json.dump(idx, open(f"{out}/fsc_index.json", "w"), ensure_ascii=False, indent=1)
