from __future__ import annotations

from folio.retrieval.base import Hit, Retriever


def build_filters(filters: dict) -> list[dict]:
    return [{"terms": {k: v}} if isinstance(v, (list, tuple, set)) else {"term": {k: v}} for k, v in filters.items()]


class OpenSearchRetriever(Retriever):
    def _hits(self, resp: dict) -> list[Hit]:
        vf = self.sink.vector_field
        out = []
        for h in resp["hits"]["hits"]:
            src = {k: v for k, v in h["_source"].items() if k != vf}
            out.append(Hit(h["_id"], src.get("doc_id", ""), h["_score"], src.get("content", ""), src))
        return out

    def _bm25(self, query, n, filters):
        body = {"size": n, "query": {"bool": {"must": [{"match": {"content": query}}], "filter": build_filters(filters)}}}
        return self._hits(self.sink.client.search(index=self.sink.index, body=body))

    def _vector(self, vector, n, filters):
        knn = {"vector": vector, "k": n}
        if filters:
            knn["filter"] = {"bool": {"filter": build_filters(filters)}}
        body = {"size": n, "query": {"knn": {self.sink.vector_field: knn}}}
        return self._hits(self.sink.client.search(index=self.sink.index, body=body))
