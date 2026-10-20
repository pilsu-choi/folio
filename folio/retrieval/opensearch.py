from __future__ import annotations

from folio.retrieval.base import Hit, Retriever, is_range


def build_filters(filters: dict) -> list[dict]:
    def one(k, v):
        if is_range(v):
            return {"range": {k: v}}
        return {"terms": {k: list(v)}} if isinstance(v, (list, tuple, set)) else {"term": {k: v}}

    return [one(k, v) for k, v in filters.items()]


class OpenSearchRetriever(Retriever):
    weighted_fields = True

    def _hits(self, resp: dict) -> list[Hit]:
        vf = self.sink.vector_field
        out = []
        for h in resp["hits"]["hits"]:
            src = {k: v for k, v in h["_source"].items() if k != vf}
            out.append(Hit(h["_id"], src.get("doc_id", ""), h["_score"], src.get("content", ""), src))
        return out

    def _match(self, query: str) -> dict:
        if self.fields == {"content": 1.0}:
            return {"match": {"content": query}}
        return {"multi_match": {"query": query, "fields": [f"{f}^{w:g}" for f, w in self.fields.items()]}}

    def _bm25(self, query, n, filters):
        body = {"size": n, "query": {"bool": {"must": [self._match(query)], "filter": build_filters(filters)}}}
        return self._hits(self.sink.client.search(index=self.sink.index, body=body))

    def _vector(self, vector, n, filters):
        knn = {"vector": vector, "k": n}
        if filters:
            knn["filter"] = {"bool": {"filter": build_filters(filters)}}
        body = {"size": n, "query": {"knn": {self.sink.vector_field: knn}}}
        return self._hits(self.sink.client.search(index=self.sink.index, body=body))
