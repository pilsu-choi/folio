"""Chunking grid: chunk -> embed -> score.   usage: grid.py chunk|embed|score <parse_dir> [embed_url]
Relevance is span-based and config-independent: a chunk is relevant to a query if it is on the span's doc/page and
its `content` contains a >=20-char window of the answer-span text, or the span straddles the chunk edge by >=6 chars."""
import hashlib, itertools, json, os, sys, time
import numpy as np
from common import OUT, load_docs, norm_map

import folio.chunk as C
from folio.eval.metrics import mrr, ndcg_at_k  # reused; recall@k is fixed-k, so span recall at a token budget is local

def name_(s, o):
    return f"{s}_max{o['max_tokens']}" + (f"_min{o['min_tokens']}" if "min_tokens" in o else "") + (f"_ov{o['overlap']}" if o.get("overlap") else "")


MAXS = [256, 512, 768, 1024, 1536, 2048]
MINS = [0, 64, 128, 256]
BUDGETS = [1000, 2000, 4000]
TOK = "bgetok:count"
CONFIGS = [("adaptive", {"max_tokens": M, "min_tokens": m}) for M in MAXS for m in MINS if m < M]
CONFIGS += [("fixed", {"max_tokens": M, "overlap": 0}) for M in MAXS]
CONFIGS += [("fixed", {"max_tokens": 512, "overlap": 64}), ("fixed", {"max_tokens": 1024, "overlap": 128})]


if os.environ.get("ONLY"):  # staged grid: ONLY="adaptive_max512_min128,fixed_max512,..." (exact config names)
    _keep = set(os.environ["ONLY"].split(","))
    CONFIGS = [(s, o) for s, o in CONFIGS if name_(s, o) in _keep]


def name(s, o):
    return f"{s}_max{o['max_tokens']}" + (f"_min{o['min_tokens']}" if "min_tokens" in o else "") + (f"_ov{o['overlap']}" if o.get("overlap") else "")


def stage_chunk(parse_dir):
    import bgetok
    docs = load_docs(parse_dir)
    os.makedirs(f"{OUT}/chunks", exist_ok=True)
    for s, o in CONFIGS:
        fn = f"{OUT}/chunks/{name(s, o)}.jsonl"
        if os.path.exists(fn): continue
        t = time.time(); rows = []
        for did, doc in docs.items():
            for r in getattr(C, s)(doc, tokenizer=TOK, **o):
                rows.append({"doc_id": did, "chunk_id": r["chunk_id"], "page": r["page"], "pages": r.get("pages") or [r["page"]],
                             "content": r["content"], "ctx": r["contextualized_content"], "ntok": bgetok.count(r["contextualized_content"])})
        with open(fn, "w") as f:
            for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(name(s, o), len(rows), round(time.time() - t, 1), flush=True)


def key(t): return hashlib.sha1(t.encode()).hexdigest()


def stage_embed(url, model="bge-m3"):
    import httpx, concurrent.futures as cf
    texts = {}
    for s, o in CONFIGS:
        for l in open(f"{OUT}/chunks/{name(s, o)}.jsonl"):
            r = json.loads(l)["ctx"]; texts[key(r)] = r
    for l in open(f"{OUT}/testset.jsonl"):
        q = json.loads(l)["question"]; texts[key(q)] = q
    cache_fn = f"{OUT}/emb_cache.npz"
    cache = dict(np.load(cache_fn)) if os.path.exists(cache_fn) else {}
    todo = sorted([k for k in texts if k not in cache], key=lambda k: len(texts[k]))
    print("unique texts", len(texts), "to embed", len(todo), flush=True)
    batches = [todo[i:i + 32] for i in range(0, len(todo), 32)]

    def go(b):
        r = httpx.post(url + "/embeddings", json={"model": model, "input": [texts[k] for k in b]}, timeout=600).json()
        return b, [d["embedding"] for d in sorted(r["data"], key=lambda d: d["index"])]
    t = time.time()
    with cf.ThreadPoolExecutor(4) as ex:
        for i, (b, vs) in enumerate(ex.map(go, batches)):
            for k, v in zip(b, vs): cache[k] = np.asarray(v, dtype=np.float32)
            if i % 50 == 0: print(i, len(batches), round(time.time() - t), flush=True)
    np.savez(cache_fn, **cache)


def edge_overlap(sp, ntext, m0=6):
    """span straddles a chunk boundary: its tail starts the chunk or its head ends the chunk (>= m0 chars)."""
    return any(ntext.startswith(sp[-m:]) for m in range(m0, len(sp))) or any(ntext.endswith(sp[:m]) for m in range(m0, len(sp)))


def trunc_norm(ctx, r):
    import bgetok
    enc = bgetok._TOK.encode(ctx, add_special_tokens=False)
    return norm_map(ctx[: enc.offsets[r - 1][1]])[0] if r < len(enc.ids) else norm_map(ctx)[0]


def grams(s, k=20):
    n, _ = norm_map(s.strip())
    if len(n) <= k: return [n]
    g = [n[i:i + k] for i in range(0, len(n) - k + 1, k // 2)]
    return g + [n[-k:]]


def stage_score(parse_dir):
    cache = dict(np.load(f"{OUT}/emb_cache.npz"))
    qs = [json.loads(l) for l in open(f"{OUT}/testset.jsonl")]
    Q = np.stack([cache[key(q["question"])] for q in qs]); Q /= np.linalg.norm(Q, axis=1, keepdims=True)
    qg = [[grams(t) for t in q["span_text"]] for q in qs]
    qn = [[norm_map(t.strip())[0] for t in q["span_text"]] for q in qs]
    res = {}
    for s, o in CONFIGS:
        cn = name(s, o)
        ch = [json.loads(l) for l in open(f"{OUT}/chunks/{cn}.jsonl")]
        E = np.stack([cache[key(c["ctx"])] for c in ch]); E /= np.linalg.norm(E, axis=1, keepdims=True)
        ntok = np.array([c["ntok"] for c in ch]); norm = [norm_map(c["content"])[0] for c in ch]
        page_idx = {}
        for i, c in enumerate(ch):
            for p in c["pages"]: page_idx.setdefault((c["doc_id"], p), []).append(i)
        S = Q @ E.T
        per = []
        for qi, q in enumerate(qs):
            span_rel = []
            for gl in qg[qi]:
                span_rel.append({i for i in page_idx.get((q["doc_id"], q["page"]), [])
                                 if any(g in norm[i] for g in gl) or edge_overlap(qn[qi][len(span_rel)], norm[i])})
            rel_ids = [ch[i]["chunk_id"] for i in set().union(*span_rel)]
            assert rel_ids, f"{cn} {q['qid']}: no chunk overlaps the answer span (mapping bug)"
            order = np.argsort(-S[qi])
            ranked = [ch[i]["chunk_id"] for i in order]
            row = {"qid": q["qid"], "n_rel": len(rel_ids), "mrr": mrr(rel_ids, ranked), "ndcg10": ndcg_at_k(rel_ids, ranked, 10)}
            for B in BUDGETS:
                cum, got, part = 0, set(), None
                for i in order:  # greedy rank order; the chunk that crosses the budget is truncated to the remaining tokens
                    r = B - cum
                    if r <= 0: break
                    if ntok[i] <= r: got.add(i); cum += ntok[i]
                    else: part = (i, r); break
                hit = []
                for sr, gl in zip(span_rel, qg[qi]):
                    h = bool(sr & got)
                    if not h and part and part[0] in sr:
                        tn = trunc_norm(ch[part[0]]["ctx"], part[1]); h = any(g in tn for g in gl)
                    hit.append(h)
                strict = [bool(sr & got) for sr in span_rel]
                row[f"rec{B}"] = sum(hit) / len(hit); row[f"all{B}"] = float(all(hit)); row[f"hit{B}"] = float(any(hit))
                row[f"strict{B}"] = sum(strict) / len(strict)
            per.append(row)
        st = {"n_chunks": len(ch), "tok_mean": float(ntok.mean()), "tok_p50": float(np.percentile(ntok, 50)),
              "tok_p90": float(np.percentile(ntok, 90)), "tok_max": int(ntok.max()), "tok_total": int(ntok.sum()),
              "frac_lt64": float((ntok < 64).mean()), "mean_n_rel": float(np.mean([r["n_rel"] for r in per]))}
        res[cn] = {"cfg": {"strategy": s, **o}, "stats": st, "per_query": per}
        print(cn, st["n_chunks"], round(float(np.mean([r["rec2000"] for r in per])), 3), flush=True)
    json.dump(res, open(f"{OUT}/scores.json", "w"))


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"chunk": lambda: stage_chunk(sys.argv[2]), "embed": lambda: stage_embed(sys.argv[2]), "score": lambda: stage_score(sys.argv[2])}[cmd]()
