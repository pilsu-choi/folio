from __future__ import annotations

import json
import re

from psycopg import sql

from folio.errors import ConfigError
from folio.retrieval.base import RANGE_OPS, Hit, Retriever, is_range


class PgVectorRetriever(Retriever):
    def _select(self, score: sql.Composable, where: list[sql.Composable],
                order: sql.Composable, params: list, n: int) -> list[Hit]:
        s = self.sink
        info = s.introspect()
        cols = [c for c in info["columns"] if c != info["vector_column"]]
        q = sql.SQL("SELECT {cols}, {score} FROM {t}{where} ORDER BY {order} LIMIT %s").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, cols)), score=score, t=s._ident(),
            where=sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where) if where else sql.SQL(""), order=order)
        with s._conn() as conn:
            rows = conn.execute(q, [*params, n]).fetchall()
        rev = {v: k for k, v in s.columns.items()}
        out = []
        for row in rows:
            rec = {rev.get(c, c): v for c, v in zip(cols, row[:-1])}
            meta = rec.pop("metadata", None)
            if isinstance(meta, dict):
                rec.update(meta)
            out.append(Hit(rec.get("chunk_id", ""), rec.get("doc_id", ""), float(row[-1]), rec.get("content", ""), rec))
        return out

    def _where(self, filters: dict) -> tuple[list[sql.Composable], list]:
        s = self.sink
        info = s.introspect()
        cols = info["columns"]
        where, params = [], []
        for k, v in filters.items():
            col = s._col(k)
            if is_range(v):
                if col in cols and cols[col] == "ARRAY":
                    raise ConfigError(f"pgvector range filter on array column {k!r}")
                for op, b in v.items():
                    if col in cols:
                        where.append(sql.SQL("{} " + RANGE_OPS[op] + " %s").format(sql.Identifier(col)))
                        params.append(b)
                    elif "metadata" in cols:  # numbers compare numerically, strings (ISO dates) as text
                        cast = "::numeric" if isinstance(b, (int, float)) else ""
                        where.append(sql.SQL(f"(metadata->>%s::text){cast} {RANGE_OPS[op]} %s{cast}"))
                        params += [k, b if cast else str(b)]
                    else:
                        raise ConfigError(f"pgvector filter on unknown column {k!r}")
                continue
            multi = isinstance(v, (list, tuple, set))
            vals = list(v) if multi else v
            if col in cols:
                ident = sql.Identifier(col)
                if cols[col] == "ARRAY":
                    where.append(sql.SQL("{} && %s::text[]").format(ident))
                    params.append(vals if multi else [vals])
                elif multi:
                    where.append(sql.SQL("{} = ANY(%s)").format(ident))
                    params.append(vals)
                else:
                    where.append(sql.SQL("{} = %s").format(ident))
                    params.append(vals)
            elif "metadata" in cols:
                if multi:  # scalar metadata value in list, or jsonb array sharing any element
                    where.append(sql.SQL("(metadata->>%s::text = ANY(%s::text[]) OR metadata->%s::text ?| %s::text[])"))
                    params += [k, [str(x) for x in vals], k, [str(x) for x in vals]]
                else:
                    where.append(sql.SQL("(metadata->>%s::text = %s::text OR metadata->%s::text @> %s::jsonb)"))
                    params += [k, str(vals), k, json.dumps(vals)]
            else:
                raise ConfigError(f"pgvector filter on unknown column {k!r}")
        return where, params

    def _vector(self, vector, n, filters):
        info = self.sink.introspect()
        vcol = sql.Identifier(info["vector_column"])
        lit = "[" + ",".join(repr(float(x)) for x in vector) + "]"
        where, params = self._where(filters)
        score = sql.SQL("1 - ({} <=> %s::vector)").format(vcol)
        order = sql.SQL("{} <=> %s::vector").format(vcol)
        return self._select(score, where, order, [lit, *params, lit], n)

    def _bm25(self, query, n, filters):
        words = re.findall(r"\w+", query)
        if not words:
            return []
        tsq = " | ".join(words)
        content = sql.Identifier(self.sink._col("content"))
        where, params = self._where(filters)
        match = sql.SQL("to_tsvector('simple', {}) @@ to_tsquery('simple', %s)").format(content)
        score = sql.SQL("ts_rank(to_tsvector('simple', {}), to_tsquery('simple', %s))").format(content)
        order = sql.SQL("ts_rank(to_tsvector('simple', {}), to_tsquery('simple', %s)) DESC").format(content)
        return self._select(score, [match, *where], order, [tsq, tsq, *params, tsq], n)
