import json, os, signal, sys, time, glob
t0 = time.perf_counter()
import hwpforge
from hwpforge import Document
imp = time.perf_counter() - t0
fd = sorted({os.path.dirname(p) for p in glob.glob('/fonts/**/*', recursive=True) if p.lower().endswith(('.ttf','.otf','.ttc'))})
def alarm(*a): raise TimeoutError("300s timeout")
signal.signal(signal.SIGALRM, alarm)
res = []
for p in sorted(glob.glob('/in/*.hwpx')):
    name = os.path.basename(p)[:-5]
    r = {"name": name, "ok": False, "seconds": None, "mode": None, "error": None}
    doc = Document.open(p)
    for mode, deg in (("strict", False), ("degraded", True)):
        signal.alarm(300); t = time.perf_counter()
        try:
            out = doc.to_pdf(font_dirs=fd, discovery="explicit", degraded=deg)
            r["seconds"] = time.perf_counter() - t; r["mode"] = mode; r["ok"] = True
            open(f'/out/{name}.pdf', 'wb').write(out.data if hasattr(out,'data') else bytes(out))
            r["report"] = repr(out.report)[:400] if hasattr(out,'report') else None
            signal.alarm(0); break
        except BaseException as e:
            signal.alarm(0)
            r["seconds"] = time.perf_counter() - t
            err = f"{type(e).__name__}: {e}"[:600]
            if mode == "strict": r["strict_error"] = err
            r["error"] = err; r["mode"] = mode
    if r["ok"]: r["error"] = r.get("strict_error")
    print(r, flush=True); res.append(r)
json.dump(res, open('/out/results.json','w'), ensure_ascii=False, indent=1)
json.dump({"import_seconds": imp, "font_dirs": fd, "version": hwpforge.__version__}, open('/out/meta.json','w'))
