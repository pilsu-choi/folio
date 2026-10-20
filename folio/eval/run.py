from __future__ import annotations

import hashlib
import json
import random
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from folio.eval import metrics as M
from folio.eval.testset import norm_map

WINDOW, EDGE = 20, 6  # a chunk covers a span: it holds a 20-char window of it, or the span straddles its edge by >= 6


def config_commit(config_dir: str | None) -> str | None:
    """Last commit touching ``config_dir`` (``git log -1 --format=%H -- <dir>``), None if unavailable."""
    if not config_dir:
        return None
    try:
        r = subprocess.run(["git", "log", "-1", "--format=%H", "--", str(config_dir)], cwd=config_dir,
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def testset_hash(testset: list[dict]) -> str:
    """Identity of the testset (queries + evidence): ``compare`` pairs qids only within the same testset."""
    key = [(it["qid"], it["query"], [(e["doc_id"], e.get("page"), e["text"]) for e in it["evidence"]]) for it in testset]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:16]


def _prepare(item: dict) -> list[dict]:
    out = []
    for e in item["evidence"]:
        n = norm_map(e["text"].strip())[0]
        grams = [n] if len(n) <= WINDOW else [n[i:i + WINDOW] for i in range(0, len(n) - WINDOW + 1, WINDOW // 2)] + [n[-WINDOW:]]
        out.append({"doc_id": e["doc_id"], "page": e.get("page"), "norm": n, "grams": grams})
    return out


def _pages(record: dict) -> set[int] | None:
    pages = record.get("pages") or ([record["page"]] if record.get("page") is not None else None)
    return {int(p) for p in pages} if pages else None


def _covered(spans: list[dict], doc_id: str, pages: set[int] | None, content: str) -> set[int]:
    """Indices of ``spans`` the chunk contains. Page is checked only when the store returned it."""
    text = norm_map(content)[0]
    out = set()
    for i, s in enumerate(spans):
        if s["doc_id"] != doc_id or (pages is not None and s["page"] is not None and s["page"] not in pages):
            continue
        n = s["norm"]
        if any(g in text for g in s["grams"]) or any(text.startswith(n[-m:]) or text.endswith(n[:m]) for m in range(EDGE, len(n))):
            out.add(i)
    return out


def _mean(rows: list[dict]) -> dict:
    return {k: sum(r[k] for r in rows) / len(rows) for k in rows[0] if k != "type"} if rows else {}


def run(testset: list[dict], retriever, ks=(1, 5, 10), config_dir: str | None = None, chunks: list[dict] | None = None,
        **search_kwargs) -> dict:
    """Search every query (``search_kwargs``: mode, filters) and score span coverage: recall@k, all@k (every span
    found), MRR, nDCG@k, overall and per question type. ``chunks`` (the index's chunk records) adds ``unreachable``:
    qids with a span no chunk covers, which would score 0 for reasons other than retrieval."""
    kmax = max(ks)
    per = {}
    for item in testset:
        spans = _prepare(item)
        hits = retriever.search(item["query"], k=kmax, **search_kwargs)
        cov = [_covered(spans, h.doc_id, _pages(h.record), h.content) for h in hits]
        n = len(spans)
        row = {"type": item.get("type", "")} | {f"recall@{k}": M.recall_at_k(cov, n, k) for k in ks}
        row |= {f"all@{k}": M.all_at_k(cov, n, k) for k in ks} | {"mrr": M.mrr(cov)}
        per[item["qid"]] = row | {f"ndcg@{k}": M.ndcg_at_k(cov, n, k) for k in ks}
    types = sorted({r["type"] for r in per.values()})
    out = {"metrics": _mean(list(per.values())), "n": len(per), "ks": list(ks),
           "by_type": {t: {"n": len(rows), "metrics": _mean(rows)} for t in types
                       if (rows := [r for r in per.values() if r["type"] == t])},
           "per_query": per, "testset_hash": testset_hash(testset), "config_commit": config_commit(config_dir),
           "timestamp": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")}
    if chunks is not None:
        by_doc: dict[str, list] = {}
        for c in chunks:
            by_doc.setdefault(c["doc_id"], []).append((c["doc_id"], _pages(c), c.get("content", "")))
        out["unreachable"] = [it["qid"] for it in testset if (sp := _prepare(it)) and set().union(
            *(_covered(sp, *r) for d in {s["doc_id"] for s in sp} for r in by_doc.get(d, []))) != set(range(len(sp)))]
    return out


def _paired(a: dict, b: dict, qids: list[str], n_boot: int, rng: random.Random) -> dict:
    """Per metric: mean a, mean b, delta (b - a) and a paired-bootstrap 95% CI of the delta."""
    out = {}
    for m in [m for m in a[qids[0]] if m != "type" and m in b[qids[0]]]:
        d = [b[q][m] - a[q][m] for q in qids]
        boots = sorted(sum(rng.choices(d, k=len(d))) / len(d) for _ in range(n_boot))
        out[m] = {"a": sum(a[q][m] for q in qids) / len(qids), "b": sum(b[q][m] for q in qids) / len(qids),
                  "delta": sum(d) / len(d), "ci95": [boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]]}
    return out


def compare(result_a: dict, result_b: dict, n_boot: int = 2000, seed: int = 0) -> dict:
    """Paired comparison on the qids both results scored (same testset), overall and per type."""
    if result_a.get("testset_hash") != result_b.get("testset_hash"):
        raise ValueError("compare needs results of the same testset (testset_hash differs)")
    a, b = result_a["per_query"], result_b["per_query"]
    qids = sorted(set(a) & set(b))
    rng = random.Random(seed)
    types = sorted({a[q]["type"] for q in qids})
    return {"a": result_a.get("config_commit"), "b": result_b.get("config_commit"), "n": len(qids),
            "metrics": _paired(a, b, qids, n_boot, rng) if qids else {},
            "by_type": {t: {"n": len(qs), "metrics": _paired(a, b, qs, n_boot, rng)}
                        for t in types if (qs := [q for q in qids if a[q]["type"] == t])}}


def save_result(result: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result.get('config_commit') or result['timestamp']}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return path


def load_result(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())
