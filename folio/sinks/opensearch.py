"""OpenSearch sink. ``index`` is an alias; physical indices are ``{index}-v{n}``.

Schema file ``{config_dir}/schemas/opensearch/{index}.json`` is the index body (settings + mappings).
``doc_id`` and ``chunk_id`` must be ``keyword`` fields (replace semantics use term / ids queries).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from folio.errors import ConfigError, FolioError
from folio.logging import log_event, ms_since


def diff_properties(desired: dict, current: dict, path: str = "") -> tuple[dict, list[str]]:
    """Return (properties to add, paths whose type/dimension changed)."""
    added: dict = {}
    changed: list[str] = []
    for name, spec in desired.items():
        here = f"{path}{name}"
        if name not in current:
            added[name] = spec
            continue
        cur = current[name]
        if "properties" in spec:
            sub_added, sub_changed = diff_properties(spec["properties"], cur.get("properties", {}), here + ".")
            if sub_added:
                added[name] = {"properties": sub_added}
            changed += sub_changed
        elif spec.get("type", "object") != cur.get("type", "object") or (
            spec.get("type") == "knn_vector" and spec.get("dimension") != cur.get("dimension")
        ):
            changed.append(here)
    return added, changed


class OpenSearchSink:
    def __init__(self, index: str, url: str | None = None, user: str | None = None, password: str | None = None,
                 refresh: bool = True, vector_field: str = "embedding", config_dir: str | None = None,
                 delete_old: bool = False, verify_certs: bool = True, client=None):
        self.index = index
        self.refresh = refresh
        self.vector_field = vector_field
        self.delete_old = delete_old
        self.schema_path = Path(config_dir or ".") / "schemas" / "opensearch" / f"{index}.json"
        if client is None:
            from opensearchpy import OpenSearch

            user = user or os.environ.get("OPENSEARCH_USER")
            password = password or os.environ.get("OPENSEARCH_PASSWORD")
            client = OpenSearch(
                hosts=[url or os.environ.get("OPENSEARCH_URL", "http://localhost:9200")],
                http_auth=(user, password) if user else None, verify_certs=verify_certs, timeout=60,
            )
        self.client = client

    # --- schema -----------------------------------------------------------------------------
    def _body(self) -> dict:
        if not self.schema_path.exists():
            raise ConfigError(f"OpenSearch schema file not found: {self.schema_path}")
        return json.loads(self.schema_path.read_text())

    def _physical(self) -> str | None:
        """Concrete index behind the alias, or None when the alias does not exist."""
        c = self.client
        if c.indices.exists_alias(name=self.index):
            return sorted(c.indices.get_alias(name=self.index))[-1]
        if c.indices.exists(index=self.index):
            raise ConfigError(f"'{self.index}' exists as a concrete index, not an alias; migrate it first")
        return None

    def expected_dimension(self) -> int | None:
        props = self._body().get("mappings", {}).get("properties", {})
        return props.get(self.vector_field, {}).get("dimension")

    def apply_schema(self, reindex: bool = False) -> dict:
        body = self._body()
        c = self.client
        current = self._physical()
        if current is None:
            name = f"{self.index}-v1"
            c.indices.create(index=name, body={**body, "aliases": {self.index: {}}})
            return {"action": "created", "index": name, "alias": self.index}
        live = c.indices.get_mapping(index=current)[current]["mappings"].get("properties", {})
        added, changed = diff_properties(body.get("mappings", {}).get("properties", {}), live)
        if reindex:
            return self._reindex(current, body)
        if changed:
            raise ConfigError(
                f"incompatible mapping change for {changed} on '{current}'; run `folio schema apply --reindex`"
            )
        if added:
            c.indices.put_mapping(index=current, body={"properties": added})
            return {"action": "mapping_updated", "index": current, "added": sorted(added)}
        return {"action": "unchanged", "index": current}

    def _reindex(self, old: str, body: dict) -> dict:
        c = self.client
        m = re.search(r"-v(\d+)$", old)
        new = f"{self.index}-v{int(m.group(1)) + 1 if m else 2}"
        if c.indices.exists(index=new):
            c.indices.delete(index=new)  # leftover from an interrupted run; never aliased
        c.indices.create(index=new, body=body)
        res = c.reindex(body={"source": {"index": old}, "dest": {"index": new}}, wait_for_completion=True, refresh=True)
        c.indices.update_aliases(body={"actions": [
            {"remove": {"index": old, "alias": self.index}}, {"add": {"index": new, "alias": self.index}}]})
        if self.delete_old:
            c.indices.delete(index=old)
        return {"action": "reindexed", "old_index": old, "index": new, "copied": res.get("total"), "deleted_old": self.delete_old}

    # --- data -------------------------------------------------------------------------------
    def upsert(self, records: list[dict]) -> None:
        from opensearchpy import helpers

        if not records:
            return
        t0 = time.perf_counter()
        by_doc: dict[str, list[str]] = {}
        actions = []
        for r in records:
            by_doc.setdefault(r["doc_id"], []).append(r["chunk_id"])
            src = dict(r)
            if self.vector_field != "embedding" and "embedding" in src:
                src[self.vector_field] = src.pop("embedding")
            actions.append({"_op_type": "index", "_index": self.index, "_id": r["chunk_id"], "_source": src})
        ok, errors = helpers.bulk(self.client, actions, raise_on_error=False, stats_only=False)
        if errors:
            raise FolioError(f"OpenSearch bulk failed for {len(errors)} records: {errors[0]}")
        self.client.indices.refresh(index=self.index)
        log_event("sink_upsert", logging.DEBUG, "folio.sinks", sink="opensearch", index=self.index, records=len(records),
                  docs=len(by_doc), ms=ms_since(t0))
        for doc_id, ids in by_doc.items():
            self.client.delete_by_query(index=self.index, conflicts="proceed", refresh=self.refresh, body={"query": {"bool": {
                "filter": [{"term": {"doc_id": doc_id}}], "must_not": [{"ids": {"values": ids}}]}}})

    def delete(self, doc_id: str) -> None:
        self.client.delete_by_query(index=self.index, conflicts="proceed", refresh=self.refresh,
                                    body={"query": {"term": {"doc_id": doc_id}}})

    def ping(self) -> bool:
        return bool(self.client.ping())

    def count(self, doc_id: str | None = None) -> int:
        self.client.indices.refresh(index=self.index)
        q = {"query": {"term": {"doc_id": doc_id}}} if doc_id else None
        return self.client.count(index=self.index, body=q)["count"]

    def close(self) -> None:
        self.client.close()
