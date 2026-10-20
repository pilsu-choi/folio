"""Milvus sink. ``collection`` is an alias; physical collections are ``{collection}_v{n}``.

Schema file ``{config_dir}/schemas/milvus/{collection}.json``::

    {
      "dynamic_field": true,                       # unknown mapper keys are stored in the dynamic field
      "fields": [                                  # type: VARCHAR INT64 INT32 BOOL FLOAT DOUBLE JSON ARRAY
        {"name": "chunk_id", "type": "VARCHAR", "max_length": 128, "is_primary": true},
        {"name": "doc_id", "type": "VARCHAR", "max_length": 128},
        {"name": "content", "type": "VARCHAR", "max_length": 65535, "enable_analyzer": true},
        {"name": "page", "type": "INT64", "nullable": true},
        {"name": "acl", "type": "ARRAY", "element_type": "VARCHAR", "max_length": 128, "max_capacity": 64},
        {"name": "embedding", "type": "FLOAT_VECTOR", "dim": 384},
        {"name": "sparse", "type": "SPARSE_FLOAT_VECTOR"}
      ],                                           # FLOAT_VECTOR / SPARSE_FLOAT_VECTOR also allowed
      "bm25": {"input_field": "content", "output_field": "sparse"},   # optional BM25 function
      "indexes": [{"field": "embedding", "index_type": "AUTOINDEX", "metric_type": "COSINE"},
                  {"field": "sparse", "index_type": "SPARSE_INVERTED_INDEX", "metric_type": "BM25"}]
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from folio.errors import ConfigError
from folio.logging import log_event, ms_since

_PAGE = 1000


def _q(value: str) -> str:
    return json.dumps(value)  # double-quoted, escaped string literal valid in Milvus expressions


def load_schema(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Milvus schema file not found: {path}")
    return json.loads(path.read_text())


def schema_signature(spec: dict) -> dict:
    """Comparable view of a schema file: {field: (type, dim, max_length)} plus flags."""
    fields = {f["name"]: (f["type"], f.get("dim"), f.get("max_length")) for f in spec["fields"]}
    return {"fields": fields, "dynamic": bool(spec.get("dynamic_field", True)), "bm25": spec.get("bm25")}


class MilvusSink:
    def __init__(self, collection: str, uri: str | None = None, token: str | None = None, vector_field: str = "embedding",
                 config_dir: str | None = None, delete_old: bool = False, client=None):
        self.collection = collection
        self.vector_field = vector_field
        self.delete_old = delete_old
        self.schema_path = Path(config_dir or ".") / "schemas" / "milvus" / f"{collection}.json"
        if client is None:
            from pymilvus import MilvusClient

            client = MilvusClient(uri=uri or os.environ.get("MILVUS_URI", "http://localhost:19530"),
                                  token=token or os.environ.get("MILVUS_TOKEN", ""))
        self.client = client

    # --- schema -----------------------------------------------------------------------------
    def _spec(self) -> dict:
        return load_schema(self.schema_path)

    def _physical(self) -> str | None:
        c = self.client
        if self.collection in c.list_collections():
            raise ConfigError(f"'{self.collection}' exists as a concrete collection, not an alias")
        try:
            return c.describe_alias(self.collection)["collection_name"]
        except Exception:
            pass
        return None

    def expected_dimension(self) -> int | None:
        for f in self._spec()["fields"]:
            if f["name"] == self.vector_field:
                return f.get("dim")
        return None

    def _create(self, name: str, spec: dict) -> None:
        from pymilvus import DataType, Function, FunctionType

        c = self.client
        schema = c.create_schema(auto_id=False, enable_dynamic_field=bool(spec.get("dynamic_field", True)))
        for f in spec["fields"]:
            kw = {k: f[k] for k in ("max_length", "dim", "max_capacity", "enable_analyzer", "analyzer_params", "nullable") if k in f}
            if f.get("is_primary"):
                kw["is_primary"] = True
            if f["type"] == "ARRAY":
                kw["element_type"] = getattr(DataType, f["element_type"])
            schema.add_field(f["name"], getattr(DataType, f["type"]), **kw)
        if spec.get("bm25"):
            b = spec["bm25"]
            schema.add_function(Function(name="bm25", input_field_names=[b["input_field"]],
                                         output_field_names=[b["output_field"]], function_type=FunctionType.BM25))
        index_params = c.prepare_index_params()
        for ix in spec.get("indexes", []):
            index_params.add_index(field_name=ix["field"], index_type=ix.get("index_type", "AUTOINDEX"),
                                   metric_type=ix.get("metric_type", "COSINE"), params=ix.get("params", {}))
        c.create_collection(collection_name=name, schema=schema, index_params=index_params, consistency_level="Strong")

    def _live_signature(self, coll: str) -> dict:
        from pymilvus import DataType

        d = self.client.describe_collection(coll)
        fields = {}
        for f in d["fields"]:
            p = f.get("params", {})
            fields[f["name"]] = (DataType(f["type"]).name, p.get("dim"), p.get("max_length"))
        return {"fields": fields, "dynamic": bool(d.get("enable_dynamic_field")),
                "bm25": bool(d.get("functions"))}

    def apply_schema(self, reindex: bool = False) -> dict:
        spec = self._spec()
        c = self.client
        current = self._physical()
        if current is None:
            name = f"{self.collection}_v1"
            if c.has_collection(name):
                c.drop_collection(name)
            self._create(name, spec)
            c.create_alias(collection_name=name, alias=self.collection)
            return {"action": "created", "collection": name, "alias": self.collection}
        want = schema_signature(spec)
        live = self._live_signature(current)
        # fields filled by a BM25 function are present in `live` too, so compare names/types directly
        same = want["fields"] == live["fields"] and want["dynamic"] == live["dynamic"] and bool(want["bm25"]) == live["bm25"]
        if same and not reindex:
            return {"action": "unchanged", "collection": current}
        m = re.search(r"_v(\d+)$", current)
        new = f"{self.collection}_v{int(m.group(1)) + 1 if m else 2}"
        if c.has_collection(new):
            c.drop_collection(new)
        self._create(new, spec)
        copied = self._copy(current, new, spec)
        c.alter_alias(collection_name=new, alias=self.collection)
        if self.delete_old:
            c.drop_collection(current)
        return {"action": "migrated", "old_collection": current, "collection": new, "copied": copied,
                "deleted_old": self.delete_old}

    def _copy(self, old: str, new: str, spec: dict) -> int:
        c = self.client
        derived = {spec["bm25"]["output_field"]} if spec.get("bm25") else set()
        it = c.query_iterator(collection_name=old, filter="", output_fields=["*"], batch_size=_PAGE)
        total = 0
        while rows := it.next():
            rows = [{k: v for k, v in r.items() if k not in derived} for r in rows]
            c.insert(collection_name=new, data=rows)
            total += len(rows)
        it.close()
        return total

    # --- data -------------------------------------------------------------------------------
    def _prepare(self, r: dict, fields: dict[str, dict], dynamic: bool) -> dict:
        row = {}
        for k, v in r.items():
            if k == "embedding" and self.vector_field != "embedding":
                k = self.vector_field
            if k in fields:
                if v is None and not fields[k].get("nullable"):
                    continue
                row[k] = v
            elif dynamic and v is not None:
                row[k] = v
        return row

    def upsert(self, records: list[dict]) -> None:
        if not records:
            return
        t0 = time.perf_counter()
        spec = self._spec()
        fields = {f["name"]: f for f in spec["fields"]}
        derived = {spec["bm25"]["output_field"]} if spec.get("bm25") else set()
        dynamic = bool(spec.get("dynamic_field", True))
        rows = [{k: v for k, v in self._prepare(r, fields, dynamic).items() if k not in derived} for r in records]
        for i in range(0, len(rows), _PAGE):
            self.client.upsert(collection_name=self.collection, data=rows[i : i + _PAGE])
        keep: dict[str, set[str]] = {}
        for r in records:
            keep.setdefault(r["doc_id"], set()).add(r["chunk_id"])
        for doc_id, ids in keep.items():
            existing = self.client.query(collection_name=self.collection, filter=f"doc_id == {_q(doc_id)}",
                                         output_fields=["chunk_id"], limit=16384)
            stale = [e["chunk_id"] for e in existing if e["chunk_id"] not in ids]
            for i in range(0, len(stale), _PAGE):
                self.client.delete(collection_name=self.collection, ids=stale[i : i + _PAGE])
        log_event("sink_upsert", logging.DEBUG, "folio.sinks", sink="milvus", collection=self.collection, records=len(records), docs=len(keep), ms=ms_since(t0))

    def delete(self, doc_id: str) -> None:
        self.client.delete(collection_name=self.collection, filter=f"doc_id == {_q(doc_id)}")

    def ping(self) -> bool:
        try:
            self.client.list_collections()
            return True
        except Exception:
            return False

    def count(self, doc_id: str | None = None) -> int:
        flt = f"doc_id == {_q(doc_id)}" if doc_id else 'chunk_id != ""'
        res = self.client.query(collection_name=self.collection, filter=flt, output_fields=["count(*)"])
        return int(res[0]["count(*)"])

    def close(self) -> None:
        self.client.close()
