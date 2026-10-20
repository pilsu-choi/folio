"""pgvector sink: DML only against a table owned by the App. Never runs DDL.

Columns and the vector dimension are introspected (information_schema / pg_attribute). Record keys are mapped to
columns by name (override with ``columns: {record_key: column}``); keys without a column go to a ``metadata`` jsonb
column if the table has one, otherwise they are dropped.
"""

from __future__ import annotations

import json
import logging
import os
import time

from folio.errors import ConfigError
from folio.logging import log_event, ms_since


class PgVectorSink:
    def __init__(self, table: str, dsn: str | None = None, columns: dict[str, str] | None = None,
                 vector_field: str = "embedding", config_dir: str | None = None, connect=None):
        self.table = table
        self.columns = dict(columns or {})
        self.vector_field = vector_field
        self.dsn = dsn or os.environ.get("PGVECTOR_DSN")
        if not self.dsn and connect is None:
            raise ConfigError("pgvector sink: no dsn (set `dsn` or PGVECTOR_DSN)")
        self._connect = connect
        self._info: dict | None = None

    def _conn(self):
        if self._connect:
            return self._connect()
        import psycopg

        return psycopg.connect(self.dsn)

    def _ident(self):
        from psycopg import sql

        parts = self.table.split(".")
        return sql.Identifier(*parts)

    def _col(self, key: str) -> str:
        return self.columns.get(key, key)

    def introspect(self, refresh: bool = False) -> dict:
        """{'columns': {name: data_type}, 'dimension': int | None, 'vector_column': str}"""
        if self._info and not refresh:
            return self._info
        schema, _, name = self.table.rpartition(".")
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT column_name, data_type, udt_name FROM information_schema.columns "
                "WHERE table_name = %s AND table_schema = COALESCE(NULLIF(%s, ''), current_schema()) "
                "ORDER BY ordinal_position", (name, schema)).fetchall()
            if not rows:
                raise ConfigError(f"pgvector table {self.table!r} not found or has no columns (the App must create it)")
            vcol = self._col(self.vector_field)
            dim = None
            if any(r[0] == vcol and r[2] == "vector" for r in rows):
                row = conn.execute(
                    "SELECT atttypmod FROM pg_attribute WHERE attrelid = %s::regclass AND attname = %s",
                    (self.table, vcol)).fetchone()
                dim = row[0] if row and row[0] > 0 else None
        self._info = {"columns": {r[0]: r[2] if r[1] == "USER-DEFINED" else r[1] for r in rows},
                      "dimension": dim, "vector_column": vcol}
        return self._info

    def expected_dimension(self) -> int | None:
        return self.introspect()["dimension"]

    def apply_schema(self, reindex: bool = False) -> dict:
        return {"skipped": "pgvector tables are owned by the App"}

    def _row(self, r: dict, cols: dict) -> dict:
        row: dict = {}
        extra: dict = {}
        for k, v in r.items():
            col = self._col(k)
            if k == "embedding" and self.vector_field != "embedding":
                col = self._col(self.vector_field)
            if col in cols:
                if col == self._info["vector_column"] and v is not None:
                    v = "[" + ",".join(repr(float(x)) for x in v) + "]"
                elif cols[col] in ("json", "jsonb") and not isinstance(v, str) or isinstance(v, (dict,)):
                    v = json.dumps(v)
                row[col] = v
            else:
                extra[k] = v
        if extra and "metadata" in cols and "metadata" not in row:
            row["metadata"] = json.dumps(extra, default=str)
        return row

    def upsert(self, records: list[dict]) -> None:
        from psycopg import sql

        if not records:
            return
        t0 = time.perf_counter()
        info = self.introspect()
        cols = info["columns"]
        doc_col = self._col("doc_id")
        doc_ids = sorted({r["doc_id"] for r in records})
        with self._conn() as conn:  # single transaction, committed on exit
            conn.execute(sql.SQL("DELETE FROM {} WHERE {} = ANY(%s)").format(self._ident(), sql.Identifier(doc_col)), (doc_ids,))
            with conn.cursor() as cur:
                for r in records:
                    row = self._row(r, cols)
                    q = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        self._ident(), sql.SQL(", ").join(map(sql.Identifier, row)),
                        sql.SQL(", ").join(
                            sql.SQL("%s::vector") if c == info["vector_column"] else sql.SQL("%s") for c in row))
                    cur.execute(q, list(row.values()))
        log_event("sink_upsert", logging.DEBUG, "folio.sinks", sink="pgvector", records=len(records), docs=len(doc_ids), ms=ms_since(t0))

    def delete(self, doc_id: str) -> None:
        from psycopg import sql

        with self._conn() as conn:
            conn.execute(sql.SQL("DELETE FROM {} WHERE {} = %s").format(self._ident(), sql.Identifier(self._col("doc_id"))), (doc_id,))

    def ping(self) -> bool:
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def count(self, doc_id: str | None = None) -> int:
        from psycopg import sql

        with self._conn() as conn:
            if doc_id is None:
                return conn.execute(sql.SQL("SELECT count(*) FROM {}").format(self._ident())).fetchone()[0]
            return conn.execute(sql.SQL("SELECT count(*) FROM {} WHERE {} = %s").format(
                self._ident(), sql.Identifier(self._col("doc_id"))), (doc_id,)).fetchone()[0]

    def close(self) -> None:
        pass
