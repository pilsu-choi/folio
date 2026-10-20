from __future__ import annotations

import math
from collections.abc import Sequence


def recall_at_k(relevant: Sequence[str], ranked: Sequence[str], k: int) -> float:
    rel = set(relevant)
    return len(rel & set(ranked[:k])) / len(rel) if rel else 0.0


def mrr(relevant: Sequence[str], ranked: Sequence[str]) -> float:
    rel = set(relevant)
    for i, cid in enumerate(ranked, 1):
        if cid in rel:
            return 1.0 / i
    return 0.0


def ndcg_at_k(relevant: Sequence[str], ranked: Sequence[str], k: int) -> float:
    rel = set(relevant)
    dcg = sum(1 / math.log2(i + 1) for i, cid in enumerate(ranked[:k], 1) if cid in rel)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(len(rel), k) + 1))
    return dcg / ideal if ideal else 0.0
