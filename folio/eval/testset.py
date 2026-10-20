"""Span-based testset: answers are verbatim source-text spans (doc, page, text), not chunk ids, so one testset scores
any chunking config. Page text = the chunker's leaf texts (`build_leaves`), so every span is reachable by some chunk."""

from __future__ import annotations

import json
import os
import random
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from folio.chunk.structure import build_leaves
from folio.contracts import Document
from folio.errors import FolioError
from folio.generation import http as chat

SINGLE = ("fact", "table", "section")  # one page
SAME_DOC = ("multi_hop", "comparison", "temporal", "multi_intent")  # two pages of one document
CROSS_DOC = ("multi_document", "comparison")  # two pages of different documents
TYPES = tuple(dict.fromkeys(SINGLE + SAME_DOC + CROSS_DOC))

_DESC = {
    "fact": "사실 조회: 단일 문장·수치 하나로 답이 나오는 질문",
    "table": "표 셀 조회: 표의 특정 행·열 값을 묻는 질문. 표가 없으면 생략",
    "section": "여러 문단·항목을 종합해야 답할 수 있는 질문. 근거 인용 2개 이상",
    "multi_hop": "한 페이지의 정보로 다른 페이지의 정보를 찾아야 답이 나오는 연결 추론 질문",
    "comparison": "두 페이지에 나온 두 대상(수치·조건·항목)을 비교하는 질문",
    "temporal": "시점·기간·개정 전후처럼 시간 관계를 따져야 하는 질문",
    "multi_intent": "한 질문 안에 서로 다른 요구 두 가지를 담은 질문(각 요구의 답이 다른 페이지에 있음)",
    "multi_document": "두 문서의 내용을 종합해야 답할 수 있는 질문",
}
_PROMPT = """아래는 한국어 문서 페이지 본문이다. 이 본문만을 근거로 검색 평가용 질문을 만들어라.
만들 유형(유형마다 최대 1개, 본문으로 자연스럽게 만들 수 없는 유형은 생략):
{types}
규칙:
- 질문은 문서 없이 단독으로 읽어도 무엇을 묻는지 알 수 있어야 한다(고유명사·기관·연도·항목명을 포함). "이 표", "본문", "해당 페이지", "페이지 A" 같은 지시어 금지.
- 질문 문장을 본문 문장과 똑같이 베끼지 말고 자연스러운 검색 질문으로 쓴다.
- evidence는 답의 근거가 되는 본문 구절을 **한 글자도 바꾸지 말고 그대로** 복사한 것이다. 각 인용은 10~300자. 표는 해당 행(`| ... |` 한 줄)을 그대로 인용한다. page는 인용한 본문의 페이지 표시({labels})다.
{multi}- 번호·기호만 있는 쓸모없는 질문, 머리말·쪽번호에 대한 질문은 만들지 않는다.
JSON 배열만 출력: [{{"type":"...","question":"...","answer":"...","evidence":[{{"page":"A","quote":"..."}}]}}]

{pages}"""
_MULTI_RULE = "- 모든 질문은 두 페이지(A, B)의 근거가 모두 필요해야 하고, evidence에 A와 B의 인용을 각각 1개 이상 넣는다.\n"
_SENT = re.compile(r"(?<=[.!?。다])\s+|\n+")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def norm_map(s: str) -> tuple[str, list[int]]:
    """Whitespace-collapsed text (no spaces next to '|') and an index map back to original offsets."""
    chars, idx, n, i = [], [], len(s), 0
    while i < n:
        c = s[i]
        if c.isspace():
            j = i
            while j < n and s[j].isspace():
                j += 1
            if chars and chars[-1] != "|" and j < n and s[j] != "|":
                chars.append(" ")
                idx.append(i)
            i = j
        else:
            chars.append(c)
            idx.append(i)
            i += 1
    return "".join(chars), idx


def page_texts(doc: Document) -> dict[int, str]:
    """{page: text}: leaf texts (what the chunker sees) joined by blank lines."""
    out: dict[int, list[str]] = {}
    for lf in build_leaves(doc, False):
        out.setdefault(lf.page, []).append(lf.text)
    return {p: "\n\n".join(t) for p, t in out.items()}


def _table_headers(doc: Document) -> dict[int, list[str]]:
    """{page: [normalized table header]}: repeated in every split chunk of a table, so useless as evidence."""
    out: dict[int, list[str]] = {}
    for lf in build_leaves(doc, False):
        if lf.kind == "table" and lf.header:
            out.setdefault(lf.page, []).append(norm_map(lf.header)[0])
    return out


def _grams(s: str, k: int = 3) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i:i + k] for i in range(len(s) - k + 1)}


def _partner(key, cands, grams):
    """Most similar candidate page by character 3-gram Jaccard (works for Korean without a tokenizer)."""
    a = grams[key]
    best = max(cands, key=lambda c: len(a & grams[c]) / (len(a | grams[c]) or 1), default=None)
    return best if best and a & grams[best] else None


def _parse_items(reply: str) -> list[dict]:
    m = re.search(r"\[.*\]", _THINK.sub("", reply), re.DOTALL)
    if not m:
        raise ValueError("no JSON array in reply")
    return [x for x in json.loads(m.group(0)) if isinstance(x, dict)]


def _heuristic(text: str) -> tuple[str, str] | None:
    """(query, evidence) from the longest sentence of 20~200 chars: query = its first 12 words."""
    sents = [s.strip() for s in _SENT.split(text) if 20 <= len(s.strip()) <= 200 and "|" not in s]
    if not sents:
        return None
    best = max(sents, key=len)
    return " ".join(best.split()[:12]).strip(" .,;:"), best


def generate(docs: list[Document], pages: int = 50, pairs: int = 20, method: str = "llm",
             types: tuple[str, ...] = TYPES, llm_url: str | None = None, model: str | None = None,
             api_key: str | None = None, extra_body: dict | None = None, seed: int = 0, workers: int = 4,
             min_chars: int = 250) -> tuple[list[dict], dict]:
    """Sample ``pages`` single pages and ``pairs`` page pairs (seeded; up to 5 pages per document first) and ask the
    LLM for questions with verbatim evidence. Returns ``(items, stats)``; an item is
    ``{qid, type, query, answer, evidence: [{doc_id, page, text, start, end}]}`` (``start``/``end`` = page-text
    offsets, informational). Dropped questions are counted per reason and type in ``stats``."""
    if method not in ("heuristic", "llm"):
        raise ValueError("method must be 'heuristic' or 'llm'")
    if unknown := set(types) - set(TYPES):
        raise ValueError(f"unknown question types: {sorted(unknown)} (known: {list(TYPES)})")
    if method == "heuristic":  # offline/dev: single-page fact questions only
        types, pairs = ("fact",), 0
    url = llm_url or os.environ.get("FOLIO_LLM_URL") or os.environ.get("FOLIO_VLM_URL")
    if method == "llm" and not url:
        raise ValueError("llm method needs llm_url (or FOLIO_LLM_URL / FOLIO_VLM_URL)")
    model = model or os.environ.get("FOLIO_LLM_MODEL") or os.environ.get("FOLIO_VLM_MODEL", "")
    rng = random.Random(seed)
    text = {(d.doc_id, p): t for d in docs for p, t in page_texts(d).items() if len(t) >= min_chars}
    hdrs = {d.doc_id: _table_headers(d) for d in docs}
    by_doc: dict[str, list] = {}
    for key in sorted(text):
        by_doc.setdefault(key[0], []).append(key)

    def sample(n: int) -> list:
        first = [k for ks in by_doc.values() for k in rng.sample(ks, min(len(ks), 5))]
        rest = [k for ks in by_doc.values() for k in ks if k not in first]
        picked = rng.sample(first, min(n, len(first)))
        return sorted(picked + rng.sample(rest, max(0, min(n - len(picked), len(rest)))))

    single = [t for t in types if t in SINGLE]
    same = [t for t in types if t in SAME_DOC]
    cross = [t for t in types if t in CROSS_DOC]
    units = [([k], single) for k in sample(pages)] if single and pages else []
    if pairs and (same or cross):
        grams = {k: _grams(v[:5500]) for k, v in text.items()}
        for i, k in enumerate(sample(pairs)):  # alternate same-doc / cross-doc pairs when both are wanted
            is_cross = bool(cross) and (not same or i % 2 == 1 or len(by_doc[k[0]]) < 2)
            cands = [c for c in text if (c[0] != k[0]) == is_cross and c != k]
            if other := _partner(k, rng.sample(cands, min(len(cands), 200)), grams):
                units.append(([k, other], cross if is_cross else same))

    def ask(unit) -> list[dict] | None:
        keys, kinds = unit
        if method == "heuristic":
            got = _heuristic(text[keys[0]])
            return [{"type": "fact", "question": got[0], "evidence": [{"page": "A", "quote": got[1]}]}] if got else []
        labels = "AB"[: len(keys)]
        body = "\n\n".join(f"[페이지 {lb}] (문서: {k[0]}, {k[1]}쪽)\n\"\"\"\n{text[k][:5500]}\n\"\"\"" for lb, k in zip(labels, keys))
        prompt = _PROMPT.format(types="\n".join(f"- {t}: {_DESC[t]}" for t in kinds), labels=", ".join(labels),
                                multi=_MULTI_RULE if len(keys) > 1 else "", pages=body)
        for _ in range(2):  # one retry on a malformed reply or an LLM error
            try:
                return _parse_items(chat([{"role": "user", "content": prompt}], url=url, model=model, api_key=api_key,
                                         temperature=0.3, max_tokens=2500, extra_body=extra_body))
            except (ValueError, KeyError, FolioError):
                pass
        return None

    reasons = ("wrong_type", "no_evidence", "unlocated", "one_side_only", "header_only", "dup")
    stats = {"units": len(units), "failed_units": 0, "generated": Counter(), "kept": Counter(),
             **{r: Counter() for r in reasons}}
    seen, items = set(), []
    with ThreadPoolExecutor(workers if method == "llm" else 1) as ex:
        for (keys, kinds), got in zip(units, ex.map(ask, units)):
            if got is None:
                stats["failed_units"] += 1
                continue
            norms = [norm_map(text[k]) for k in keys]
            labels = "AB"[: len(keys)]
            for it in got:
                kind = it.get("type")
                stats["generated"][kind] += 1
                if kind not in kinds:
                    stats["wrong_type"][kind] += 1
                    continue
                q = (it.get("question") or "").strip()
                ev = [e for e in it.get("evidence") or [] if isinstance(e, dict) and isinstance(e.get("quote"), str)]
                if not q or not ev or any(len(e["quote"].strip()) < 10 for e in ev):
                    stats["no_evidence"][kind] += 1
                    continue
                spans = []
                for e in ev:  # look on the labelled page first, then on the other one
                    ne = norm_map(e["quote"].strip())[0]
                    lb = str(e.get("page", "A"))[:1]
                    pref = labels.index(lb) if lb and lb in labels else 0
                    j, i = next(((j, i) for j in (pref, 1 - pref)[: len(keys)] if (i := norms[j][0].find(ne)) >= 0), (None, -1))
                    if j is None:
                        spans = None
                        break
                    (did, page), idx = keys[j], norms[j][1]
                    a, b = idx[i], idx[i + len(ne) - 1] + 1
                    spans.append({"doc_id": did, "page": page, "text": text[keys[j]][a:b], "start": a, "end": b})
                if spans is None:
                    stats["unlocated"][kind] += 1
                    continue
                if len(keys) > 1 and len({(s["doc_id"], s["page"]) for s in spans}) < 2:
                    stats["one_side_only"][kind] += 1
                    continue
                if all(any(norm_map(s["text"])[0] in h for h in hdrs[s["doc_id"]].get(s["page"], [])) for s in spans):
                    stats["header_only"][kind] += 1
                    continue
                if q in seen:
                    stats["dup"][kind] += 1
                    continue
                seen.add(q)
                stats["kept"][kind] += 1
                items.append({"qid": f"q{len(items):04d}", "type": kind, "query": q, "answer": it.get("answer") or "",
                              "evidence": spans})
    if units and stats["failed_units"] == len(units):
        raise FolioError(f"testset: every LLM call failed ({len(units)}); check the LLM url/model ({url}, {model!r})")
    return items, {k: dict(v) if isinstance(v, Counter) else v for k, v in stats.items()}
