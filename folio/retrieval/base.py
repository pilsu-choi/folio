from __future__ import annotations

from dataclasses import dataclass, field


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


class Retriever:
    """Template: subclasses implement ``_bm25`` and ``_vector`` returning ranked hits."""

    def __init__(self, sink, embed):
        self.sink = sink
        self.embed = embed

    def _bm25(self, query: str, n: int, filters: dict) -> list[Hit]:
        raise NotImplementedError

    def _vector(self, vector: list[float], n: int, filters: dict) -> list[Hit]:
        raise NotImplementedError

    def search(self, query: str, k: int = 10, filters: dict | None = None, mode: str = "hybrid") -> list[Hit]:
        if mode not in ("hybrid", "bm25", "vector"):
            raise ValueError(f"mode must be hybrid, bm25 or vector, got {mode!r}")
        filters = {key: v for key, v in (filters or {}).items() if v is not None}
        n = max(k * 3, 30)
        if mode == "bm25":
            return self._bm25(query, n, filters)[:k]
        vec = self.embed([query])[0]
        if mode == "vector":
            return self._vector(vec, n, filters)[:k]
        return rrf([self._bm25(query, n, filters), self._vector(vec, n, filters)], k)
