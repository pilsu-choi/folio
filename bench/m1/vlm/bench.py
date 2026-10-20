"""Run VLM tasks against http://localhost:8010 for one served model. usage: bench.py <model> <outdir>
Raw outputs: <outdir>/<model>/<task>__<id>.json  {text, sec, finish, tokens}"""
import base64, json, os, sys, time, urllib.request
S = os.path.expanduser("~/folio-m1-bench/samples")
PROMPT = ("Convert this document page to Markdown. Use # for headings, '-' for list items and GitHub "
          "pipe tables for tables. Output only the Markdown.")  # == folio/parse/vlm.py PROMPT (asserted below)
src = open(os.path.expanduser("~/folio-m1-bench/folio-src/folio/parse/vlm.py")).read()
assert "Convert this document page to Markdown. Use # for headings, '-' for list items and GitHub " in src
TABLE_HTML = ("Convert the table in this image to HTML. Use <table>, <tr>, <th>/<td> and use rowspan/colspan "
              "for merged cells. Output only the HTML.")
FIG = ("이 그림(차트/도식)을 한국어로 자세히 설명하세요. 제목, 범례, 축, 모든 수치와 라벨, 항목 간 관계와 추세를 "
       "빠짐없이 포함하고, 그림에 없는 내용은 쓰지 마세요.")
NATIVE = {  # doc-VLM native task prompts (extra runs)
    "PaddleOCR-VL-1.6": {"scan": "OCR:", "table": "Table Recognition:", "figure": "Chart Recognition:"},
    "GLM-OCR": {"scan": "Text Recognition:", "table": "Table Recognition:"},
}

def call(model, path, prompt, max_tokens=4096):
    mime = "image/jpeg" if path.endswith((".jpg", ".jpeg")) else "image/png"
    b = base64.b64encode(open(path, "rb").read()).decode()
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0, "messages": [{"role": "user", "content": [
        {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b}"}}]}]}
    t = time.time()
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:8010/v1/chat/completions",
                      json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=600))
        c = r["choices"][0]
        return {"text": c["message"]["content"] or "", "sec": round(time.time() - t, 2), "finish": c["finish_reason"],
                "tokens": r["usage"]["completion_tokens"]}
    except Exception as e:
        return {"text": "", "sec": round(time.time() - t, 2), "error": repr(e)[:300]}

def main():
    model, out = sys.argv[1], os.path.expanduser(sys.argv[2])
    only = sys.argv[3].split(",") if len(sys.argv) > 3 else None
    os.makedirs(f"{out}/{model}", exist_ok=True)
    man = json.load(open(f"{S}/manifest.json"))
    nat = NATIVE.get(model, {})
    jobs = []
    for m in man:
        if m["kind"] == "scan": jobs.append(("scan", m, PROMPT)); nat.get("scan") and jobs.append(("scannative", m, nat["scan"]))
        if m["kind"] == "table":
            jobs.append(("tablemd", m, PROMPT)); jobs.append(("tablehtml", m, TABLE_HTML))
            nat.get("table") and jobs.append(("tablenative", m, nat["table"]))
        if m["kind"] == "figure":
            jobs.append(("figure", m, FIG)); nat.get("figure") and jobs.append(("fignative", m, nat["figure"]))
    jobs.append(("warm", man[-1], "x")) if False else None
    # warm-up (not recorded) so CUDA graph/compile does not skew sec/page
    first = next(m for m in man if m["kind"] == "scan")
    call(model, f"{S}/{first['file']}", PROMPT, 64)
    for task, m, prompt in jobs:
        if only and task not in only: continue
        f = f"{out}/{model}/{task}__{m['id']}.json"
        if os.path.exists(f): continue
        path = f"{S}/{m['file']}"
        if m["kind"] == "table": path = path.replace(".pdf", ".png")
        res = call(model, path, prompt)
        res.update(id=m["id"], task=task, prompt=prompt)
        json.dump(res, open(f, "w"), ensure_ascii=False)
        print(task, m["id"], res["sec"], res.get("finish"), res.get("error", ""), flush=True)
main()
