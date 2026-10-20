from __future__ import annotations

import json

from folio.retrieval.base import RANGE_OPS, Hit, Retriever, is_range


def _lit(v) -> str:
    return json.dumps(v)  # strings quoted/escaped, bool -> true/false, numbers as-is


def build_filter(filters: dict, fields: dict[str, dict]) -> str:
    """Milvus boolean expression. ARRAY fields use array_contains_any; other list values use `in`; a dict is a range.
    Field names are validated identifiers (``check_filters``)."""
    parts = []
    for k, v in filters.items():
        if is_range(v):
            parts += [f"{k} {RANGE_OPS[op]} {_lit(b)}" for op, b in v.items()]
        elif isinstance(v, (list, tuple, set)):
            lst = "[" + ", ".join(_lit(x) for x in v) + "]"
            parts.append(f"array_contains_any({k}, {lst})" if fields.get(k, {}).get("type") == "ARRAY" else f"{k} in {lst}")
        else:
            parts.append(f"{k} == {_lit(v)}")
    return " and ".join(parts)


class MilvusRetriever(Retriever):
    def __init__(self, sink, embed, **settings):
        super().__init__(sink, embed, **settings)
        spec = sink._spec()
        self.fields = {f["name"]: f for f in spec["fields"]}
        self.bm25 = spec.get("bm25")
        self.drop = {sink.vector_field} | ({self.bm25["output_field"]} if self.bm25 else set())
        metrics = {i["field"]: i.get("metric_type", "COSINE") for i in spec.get("indexes", [])}
        self.metric = metrics.get(sink.vector_field, "COSINE")

    def _hits(self, res) -> list[Hit]:
        out = []
        for h in res[0]:
            ent = {k: v for k, v in h["entity"].items() if k not in self.drop}
            out.append(Hit(ent.get("chunk_id") or h.get("chunk_id") or h.get("id", ""), ent.get("doc_id", ""), h["distance"], ent.get("content", ""), ent))
        return out

    def _search(self, data, field, n, filters, params):
        return self._hits(self.sink.client.search(
            collection_name=self.sink.collection, data=data, anns_field=field, limit=n,
            filter=build_filter(filters, self.fields), output_fields=["*"], search_params=params))

    def _bm25(self, query, n, filters):
        if not self.bm25:
            return []  # no BM25 function in the schema: hybrid degrades to dense only
        return self._search([query], self.bm25["output_field"], n, filters, {"params": {}})

    def _vector(self, vector, n, filters):
        return self._search([vector], self.sink.vector_field, n, filters, {"metric_type": self.metric, "params": {}})
