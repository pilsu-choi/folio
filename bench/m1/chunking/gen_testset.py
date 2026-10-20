"""Generate Korean retrieval queries from source pages with a local vLLM (OpenAI API), keep only queries whose
evidence quotes occur verbatim in the page text. Stores char spans (page text offsets) + doc/page, NO chunk_ids.
usage: gen_testset.py <parse_dir> <llm_url> <model> <out.jsonl> [target_pages]"""
import json, random, re, sys, concurrent.futures as cf
import httpx
from common import load_docs, page_texts, norm_map, page_headers

parse_dir, url, model, out = sys.argv[1:5]
NPAGES = int(sys.argv[5]) if len(sys.argv) > 5 else 140
random.seed(7)
docs = load_docs(parse_dir)
pages = {}
for did, doc in docs.items():
    for p, t in page_texts(doc).items():
        if len(t) >= 250:
            pages[(did, p)] = t
by_doc = {}
for k in pages: by_doc.setdefault(k[0], []).append(k)
# stratified: at least 5 pages per doc (or all), rest proportional to doc length
chosen = []
for did, ks in by_doc.items():
    random.shuffle(ks); chosen += ks[: min(len(ks), 5)]
rest = [k for ks in by_doc.values() for k in ks if k not in chosen]
random.shuffle(rest); chosen += rest[: max(0, NPAGES - len(chosen))]
chosen.sort()

PROMPT = """아래는 한국어 문서의 한 페이지 본문이다(문서: {doc}). 이 페이지만을 근거로 검색 평가용 질문 {n}개를 만들어라.
유형: fact(사실 조회: 단일 문장/수치 하나로 답이 나오는 질문), table(표 셀 조회: 표의 특정 행·열 값을 묻는 질문. 표가 없으면 생략), section(여러 문단/항목을 종합해야 답할 수 있는 질문; 근거 인용 2개 이상).
규칙:
- 질문은 문서 없이 단독으로 읽어도 무엇을 묻는지 알 수 있어야 한다(고유명사·기관·연도·항목명을 포함). "이 표", "본문", "해당 페이지" 같은 지시어 금지.
- 질문 문장을 본문 문장과 똑같이 베끼지 말고 자연스러운 검색 질문으로 쓴다.
- evidence는 답의 근거가 되는 본문 구절을 **한 글자도 바꾸지 말고 그대로** 복사한 문자열 목록이다. 각 인용은 10~300자. 표는 해당 행(`| ... |` 한 줄)을 그대로 인용한다.
- 번호·기호만 있는 쓸모없는 질문, 머리말/쪽번호에 대한 질문은 만들지 않는다.
JSON 배열만 출력: [{{"type":"fact|table|section","question":"...","answer":"...","evidence":["...","..."]}}]

본문:
\"\"\"
{text}
\"\"\""""

def ask(key):
    did, p = key
    text = pages[key][:5500]
    n = 2 if len(pages[key]) < 1500 else 3
    body = {"model": model, "temperature": 0.3, "max_tokens": 2500,
            "messages": [{"role": "user", "content": PROMPT.format(doc=did, n=n, text=text)}],
            "chat_template_kwargs": {"enable_thinking": False}}
    for _ in range(2):
        try:
            r = httpx.post(url + "/chat/completions", json=body, timeout=300).json()
            s = r["choices"][0]["message"]["content"]
            m = re.search(r"\[.*\]", s, re.S)
            items = [x for x in json.loads(m.group(0)) if isinstance(x, dict)]
            open(out + '.raw', 'a').write(json.dumps({'key': key, 'items': items}, ensure_ascii=False) + '\n')
            return key, items
        except Exception as e:
            err = e
    return key, []

from collections import Counter
hdrs = {did: page_headers(doc) for did, doc in docs.items()}
stats = {"generated": 0, "no_evidence": 0, "unlocated": 0, "header_only": 0, "dup": 0, "kept": 0}
by_type = {"generated": Counter(), "kept": Counter()}
seen, rows = set(), []
with cf.ThreadPoolExecutor(8) as ex:
    for key, items in ex.map(ask, chosen):
        did, p = key
        full = pages[key]; nfull, idx = norm_map(full)
        for it in items:
            stats["generated"] += 1; by_type["generated"][it.get("type")] += 1
            q = (it.get("question") or "").strip(); ev = [e for e in (it.get("evidence") or []) if isinstance(e, str)]
            if not q or not ev or any(len(e.strip()) < 10 for e in ev):
                stats["no_evidence"] += 1; continue
            spans = []
            for e in ev:
                ne, _ = norm_map(e.strip()); i = nfull.find(ne)
                if i < 0: spans = None; break
                s0 = idx[i]; e0 = idx[i + len(ne) - 1] + 1
                spans.append([s0, e0])
            if spans is None: stats["unlocated"] += 1; continue
            if q in seen: stats["dup"] += 1; continue
            nsp = [norm_map(t)[0] for t in (full[a:b] for a, b in spans)]
            if any(h and all(n in h for n in nsp) for h in hdrs[did].get(p, [])):
                stats["header_only"] += 1; continue
            seen.add(q); stats["kept"] += 1; by_type["kept"][it.get("type")] += 1
            rows.append({"qid": f"q{len(rows):04d}", "doc_id": did, "page": p, "type": it.get("type"), "question": q,
                         "answer": it.get("answer"), "spans": spans, "span_text": [full[a:b] for a, b in spans]})
with open(out, "w") as f:
    for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(json.dumps({"pages_sampled": len(chosen), **stats, "by_type": {k: dict(v) for k, v in by_type.items()}}, ensure_ascii=False))
