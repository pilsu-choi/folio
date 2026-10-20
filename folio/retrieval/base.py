from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from folio.errors import ConfigError

RANGE_OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    score: float
    content: str
    record: dict = field(default_factory=dict)


def rrf(rankings: list[list[Hit]], k: int, c: int = 60) -> list[Hit]:
    """Reciprocal Rank Fusion: score = sum 1 / (c + rank). Ties keep first-seen order (deterministic)."""
    scores: dict[str, float] = {}
    first: dict[str, Hit] = {}
    for ranking in rankings:
        for rank, h in enumerate(ranking, 1):
            scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + 1.0 / (c + rank)
            first.setdefault(h.chunk_id, h)
    order = sorted(scores, key=lambda cid: -scores[cid])
    return [Hit(h.chunk_id, h.doc_id, scores[cid], h.content, h.record) for cid in order[:k] if (h := first[cid])]


def is_range(value) -> bool:
    return isinstance(value, dict)


def check_filters(filters: dict | None) -> dict:
    """Drop ``None`` values and validate: field names are identifiers, list elements are scalars, a dict value is a
    range over ``gt``/``gte``/``lt``/``lte`` with scalar bounds. Raises ``ValueError``."""
    out = {}
    for k, v in (filters or {}).items():
        if v is None:
            continue
        if not isinstance(k, str) or not _FIELD.match(k):
            raise ValueError(f"invalid filter field {k!r}")
        if isinstance(v, (list, tuple, set)) and any(isinstance(x, (dict, list, tuple, set)) or x is None for x in v):
            raise ValueError(f"filter {k!r}: list elements must be scalars")
        if is_range(v):
            if not v or set(v) - set(RANGE_OPS):
                raise ValueError(f"filter {k!r}: range keys must be among {sorted(RANGE_OPS)}, got {sorted(v)}")
            if any(b is None or isinstance(b, (dict, list, tuple, set, bool)) for b in v.values()):
                raise ValueError(f"filter {k!r}: range bounds must be numbers or strings")
        out[k] = v
    return out


class Retriever:
    """Template: subclasses implement ``_bm25`` and ``_vector`` returning ranked hits.

    Settings (the pipeline YAML ``retrieval:``) are defaults; ``search`` may override ``k`` and ``mode``.
    ``rerank`` is ``(query, hits) -> hits``; it sees the fused candidates before the cut to ``k``.
    """

    weighted_fields = False  # True: the keyword leg can weight several fields; else only {"content": w}

    def __init__(self, sink, embed, *, mode: str = "hybrid", k: int = 10, fields: dict[str, float] | None = None,
                 rrf_k: int = 60, candidates: int | None = None, rerank: Callable | None = None):
        self.sink = sink
        self.embed = embed
        self.mode, self.k, self.rrf_k, self.candidates, self.rerank = mode, k, rrf_k, candidates, rerank
        self.fields = dict(fields or {"content": 1.0})
        if not self.weighted_fields and set(self.fields) != {"content"}:
            raise ConfigError(f"{type(self).__name__}: retrieval.fields supports only 'content', got {sorted(self.fields)}")

    def _bm25(self, query: str, n: int, filters: dict) -> list[Hit]:
        raise NotImplementedError

    def _vector(self, vector: list[float], n: int, filters: dict) -> list[Hit]:
        raise NotImplementedError

    def search(self, query: str, k: int | None = None, filters: dict | None = None, mode: str | None = None) -> list[Hit]:
        k = k or self.k
        mode = mode or self.mode
        if mode not in ("hybrid", "bm25", "vector"):
            raise ValueError(f"mode must be hybrid, bm25 or vector, got {mode!r}")
        filters = check_filters(filters)
        n = max(self.candidates or max(k * 3, 30), k)
        keep = n if self.rerank else k
        if mode == "bm25":
            hits = self._bm25(query, n, filters)[:keep]
        else:
            vec = self.embed([query])[0]
            if mode == "vector":
                hits = self._vector(vec, n, filters)[:keep]
            else:
                hits = rrf([self._bm25(query, n, filters), self._vector(vec, n, filters)], keep, self.rrf_k)
        if self.rerank and hits:
            hits = self.rerank(query, hits)
        return hits[:k]
