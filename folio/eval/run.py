from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from folio.eval import metrics as M


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


def run(testset: list[dict], retriever, ks=(1, 5, 10), config_dir: str | None = None, **search_kwargs) -> dict:
    """Search every query (``search_kwargs``: mode, filters) and average recall@k / MRR / nDCG@k."""
    kmax = max(ks)
    sums = {f"{n}@{k}": 0.0 for k in ks for n in ("recall", "ndcg")} | {"mrr": 0.0}
    for item in testset:
        ranked = [h.chunk_id for h in retriever.search(item["query"], k=kmax, **search_kwargs)]
        rel = item["relevant_chunk_ids"]
        for k in ks:
            sums[f"recall@{k}"] += M.recall_at_k(rel, ranked, k)
            sums[f"ndcg@{k}"] += M.ndcg_at_k(rel, ranked, k)
        sums["mrr"] += M.mrr(rel, ranked)
    n = len(testset)
    return {"metrics": {k: (v / n if n else 0.0) for k, v in sums.items()}, "n": n, "ks": list(ks),
            "config_commit": config_commit(config_dir), "timestamp": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")}


def compare(result_a: dict, result_b: dict) -> dict:
    """Per-metric values and delta (b - a)."""
    names = sorted(set(result_a["metrics"]) | set(result_b["metrics"]))
    out = {}
    for m in names:
        a, b = result_a["metrics"].get(m), result_b["metrics"].get(m)
        out[m] = {"a": a, "b": b, "delta": None if a is None or b is None else b - a}
    return {"a": result_a.get("config_commit"), "b": result_b.get("config_commit"), "metrics": out}


def save_result(result: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{result.get('config_commit') or result['timestamp']}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return path


def load_result(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())
