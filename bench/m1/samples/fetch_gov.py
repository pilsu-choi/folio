"""Download PDF attachments linked from a government board list page (FTC, KOSTAT).
usage: fetch_gov.py OUTDIR PREFIX LIST_URL LINK_REGEX [max]  (LINK_REGEX group0 = relative href)"""
import re, sys, os, html, json, urllib.parse, requests
out, prefix, url, rx = sys.argv[1:5]; mx = int(sys.argv[5]) if len(sys.argv) > 5 else 15
os.makedirs(out, exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
t = S.get(url, timeout=30).text
idx = []
for i, h in enumerate(dict.fromkeys(re.findall(rx, t))):
    if len(idx) >= mx: break
    u = urllib.parse.urljoin(url, html.unescape(h))
    try: r = S.get(u, timeout=90)
    except Exception as e: print("ERR", u, e); continue
    if r.content[:4] != b"%PDF": continue
    cd = r.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
    name = urllib.parse.unquote(m.group(1)) if m else ""
    if not m:
        pass
    else:
        try: name = m.group(1).encode("latin1").decode("utf8")
        except Exception: pass
    f = f"{out}/{prefix}_{i:02d}.pdf"; open(f, "wb").write(r.content)
    idx.append(dict(file=f, url=u, list_url=url, name=name, size=len(r.content))); print(f, len(r.content), name, flush=True)
json.dump(idx, open(f"{out}/{prefix}_index.json", "w"), ensure_ascii=False, indent=1)
