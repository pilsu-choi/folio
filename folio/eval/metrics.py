"""Span-coverage metrics. ``covered[r]`` = indices of the query's evidence spans the hit at rank r+1 contains;
a hit is relevant when it covers at least one span."""

from __future__ import annotations

import math
from collections.abc import Sequence


def recall_at_k(covered: Sequence[set[int]], n_spans: int, k: int) -> float:
    """Share of the evidence spans found in the top k."""
    return len(set().union(*covered[:k])) / n_spans if n_spans else 0.0


def all_at_k(covered: Sequence[set[int]], n_spans: int, k: int) -> float:
    """1 when every evidence span is in the top k (multi-evidence questions need all of them)."""
    return float(n_spans > 0 and len(set().union(*covered[:k])) == n_spans)


def mrr(covered: Sequence[set[int]]) -> float:
    return next((1.0 / i for i, c in enumerate(covered, 1) if c), 0.0)


def ndcg_at_k(covered: Sequence[set[int]], n_spans: int, k: int) -> float:
    """Binary relevance per hit; the ideal ranking has ``min(n_spans, k)`` relevant hits on top."""
    dcg = sum(1 / math.log2(i + 1) for i, c in enumerate(covered[:k], 1) if c)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(n_spans, k) + 1))
    return dcg / ideal if ideal else 0.0
