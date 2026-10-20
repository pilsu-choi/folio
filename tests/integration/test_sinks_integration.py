"""Sinks + retrieval against real containers (OpenSearch, Milvus, pgvector). Run: pytest -m integration."""

import json
import uuid

import pytest

from folio.embed.stub import embed_text
from folio.errors import ConfigError
from folio.retrieval import create_retriever
from folio.sinks import create_sink

pytestmark = pytest.mark.integration
DIM = 8


def embed(texts):
    return [embed_text(t, DIM) for t in texts]


def records(doc_id, n, tenant="t1", acl=None, text="kubernetes deployment guide", doc_type="manual"):
    out = []
    for i in range(n):
        content = f"{text} part{i} of {doc_id}"
        out.append({"doc_id": doc_id, "chunk_id": f"{doc_id}-{i}", "content": content, "page": i + 1,
                    "section_path": ["A", "B"], "source_uri": f"file:///{doc_id}.pdf", "chunk_type": "section",
                    "bbox": None, "pages": [i + 1], "chunk_strategy": "section", "tenant_id": tenant,
                    "acl": acl or ["group:all"], "doc_type": doc_type, "seq": i,
                    "embedding": embed_text(content, DIM)})
    return out


PG_DDL = """CREATE TABLE {t} (id serial PRIMARY KEY, doc_id text NOT NULL, chunk_id text, content text, page int,
  tenant_id text, acl text[], embedding vector(8), metadata jsonb)"""


class Env:
    pass


@pytest.fixture(params=["opensearch", "milvus", "pgvector"])
def env(request, config_dir):
    kind = request.param
    name = f"folio_it_{uuid.uuid4().hex[:8]}"
    e = Env()
    e.kind, e.name, e.config_dir = kind, name, config_dir
    if kind == "opensearch":
        e.url = request.getfixturevalue("opensearch_url")
        spec = {"type": "opensearch", "index": name, "url": e.url}
        src = config_dir / "schemas/opensearch"
    elif kind == "milvus":
        e.uri = request.getfixturevalue("milvus_uri")
        spec = {"type": "milvus", "collection": name, "uri": e.uri}
        src = config_dir / "schemas/milvus"
    else:
        e.dsn = request.getfixturevalue("pg_dsn")
        spec = {"type": "pgvector", "table": name, "dsn": e.dsn}
        src = None
    if src:
        (src / f"{name}.json").write_text((src / "folio_it.json").read_text())
    e.spec = spec
    e.sink = create_sink(spec, config_dir=str(config_dir))
    if kind == "pgvector":
        import psycopg

        with psycopg.connect(e.dsn) as c:
            c.execute(PG_DDL.format(t=name))
    else:
        e.sink.apply_schema()
    e.retriever = create_retriever(spec, config_dir=str(config_dir), embed=embed)
    yield e
    if kind == "opensearch":
        e.sink.client.indices.delete(index=f"{name}-v*", ignore_unavailable=True)
    elif kind == "milvus":
        c = e.sink.client
        c.drop_alias(alias=name)
        for coll in c.list_collections():
            if coll.startswith(name):
                c.drop_collection(coll)
    else:
        import psycopg

        with psycopg.connect(e.dsn) as c:
            c.execute(f"DROP TABLE IF EXISTS {name}")
    e.sink.close()


def test_upsert_idempotent_replace_and_delete(env):
    s = env.sink
    assert s.ping()
    s.upsert(records("docA", 5) + records("docB", 2))
    assert s.count() == 7
    s.upsert(records("docA", 5))  # same doc twice: count unchanged
    assert s.count() == 7 and s.count("docA") == 5
    s.upsert(records("docA", 3))  # re-ingest with fewer chunks removes stale
    assert s.count("docA") == 3 and s.count() == 5
    s.delete("docA")
    assert s.count("docA") == 0 and s.count() == 2
    s.upsert([])  # no-op


def test_dimension_detectable(env):
    assert env.sink.expected_dimension() == DIM
    wrong = embed_text("x", 16)
    assert len(wrong) != env.sink.expected_dimension()


def test_hybrid_search_tenant_and_acl_filters(env):
    s = env.sink
    s.upsert(records("t1a", 2, "t1", ["group:x"]) + records("t1b", 2, "t1", ["user:a"], doc_type="faq")
             + records("t1c", 2, "t1", ["group:y"]) + records("t2a", 3, "t2", ["group:x"]))
    r = env.retriever
    allhits = r.search("kubernetes deployment", k=20)
    assert {h.record["tenant_id"] for h in allhits} == {"t1", "t2"}  # filter is what separates them
    for mode in ("hybrid", "bm25", "vector"):
        hits = r.search("kubernetes deployment", k=20, filters={"tenant_id": "t1"}, mode=mode)
        assert hits and all(h.record["tenant_id"] == "t1" and h.doc_id.startswith("t1") for h in hits), mode
    hits = r.search("kubernetes deployment", k=20, filters={"tenant_id": "t1", "acl": ["user:a", "group:x"]})
    assert {h.doc_id for h in hits} == {"t1a", "t1b"}
    hits = r.search("kubernetes", k=20, filters={"tenant_id": "t1", "doc_type": "faq"})
    assert {h.doc_id for h in hits} == {"t1b"}
    top = r.search("part1 of t1c", k=3, filters={"tenant_id": "t1"})
    assert top[0].chunk_id == "t1c-1" and top[0].content.endswith("t1c") and "embedding" not in top[0].record
    assert r.search("kubernetes", k=3, filters={"tenant_id": "nobody"}) == []
    for mode in ("hybrid", "bm25", "vector"):  # range: real int column, string column, metadata/dynamic fields
        hits = r.search("kubernetes", k=20, filters={"tenant_id": "t1", "page": {"gte": 2}}, mode=mode)
        assert hits and {h.chunk_id for h in hits} == {"t1a-1", "t1b-1", "t1c-1"}, mode
        hits = r.search("kubernetes", k=20, filters={"tenant_id": {"gt": "t1"}}, mode=mode)
        assert {h.doc_id for h in hits} == {"t2a"}, mode
        hits = r.search("kubernetes", k=20, filters={"tenant_id": "t2", "seq": {"gte": 1, "lt": 2}}, mode=mode)
        assert [h.chunk_id for h in hits] == ["t2a-1"], mode
        hits = r.search("kubernetes", k=20, filters={"tenant_id": "t1", "doc_type": {"lt": "g"}}, mode=mode)
        assert {h.doc_id for h in hits} == {"t1b"}, mode


def test_opensearch_field_weights(env):
    if env.kind != "opensearch":
        pytest.skip("opensearch only")
    env.sink.upsert(records("faqdoc", 1, doc_type="faq") + records("mandoc", 1, text="faq handbook"))
    plain = env.retriever.search("faq", k=2, mode="bm25")
    weighted = create_retriever(env.spec, retrieval={"fields": {"content": 1, "doc_type": 5}}, sink=env.sink,
                                embed=embed).search("faq", k=2, mode="bm25")
    assert plain[0].doc_id == "mandoc" and weighted[0].doc_id == "faqdoc"


def test_opensearch_schema_evolution_and_reindex_alias_swap(env):
    if env.kind != "opensearch":
        pytest.skip("opensearch only")
    s, c = env.sink, env.sink.client
    s.upsert(records("d1", 4, "t1"))
    assert s.apply_schema()["action"] == "unchanged"  # idempotent
    path = env.config_dir / f"schemas/opensearch/{env.name}.json"
    body = json.loads(path.read_text())
    body["mappings"]["properties"]["lang"] = {"type": "keyword"}
    path.write_text(json.dumps(body))
    res = s.apply_schema()
    assert res["action"] == "mapping_updated" and res["added"] == ["lang"]
    body["mappings"]["properties"]["page"] = {"type": "keyword"}  # type change
    path.write_text(json.dumps(body))
    with pytest.raises(ConfigError, match="--reindex"):
        s.apply_schema()
    body["mappings"]["properties"]["page"] = {"type": "integer"}
    body["mappings"]["properties"]["content"] = {"type": "text", "analyzer": "standard", "fields": {"raw": {"type": "keyword"}}}
    path.write_text(json.dumps(body))
    old = f"{env.name}-v1"
    res = s.apply_schema(reindex=True)
    assert res["action"] == "reindexed" and res["index"] == f"{env.name}-v2" and res["copied"] == 4
    assert list(c.indices.get_alias(name=env.name)) == [f"{env.name}-v2"]
    assert c.indices.exists(index=old)  # old kept by default
    assert s.count() == 4
    assert env.retriever.search("kubernetes", k=5, filters={"tenant_id": "t1"})  # search continues after swap
    s.upsert(records("d1", 2, "t1"))
    assert s.count() == 2


def test_milvus_schema_change_migrates_through_alias(env):
    if env.kind != "milvus":
        pytest.skip("milvus only")
    s = env.sink
    s.upsert(records("d1", 3, "t1"))
    assert s.apply_schema()["action"] == "unchanged"
    path = env.config_dir / f"schemas/milvus/{env.name}.json"
    spec = json.loads(path.read_text())
    spec["fields"].insert(3, {"name": "lang", "type": "VARCHAR", "max_length": 16, "nullable": True})
    path.write_text(json.dumps(spec))
    res = s.apply_schema()
    assert res["action"] == "migrated" and res["collection"] == f"{env.name}_v2" and res["copied"] == 3
    assert s.count() == 3
    assert env.retriever.search("kubernetes", k=5, filters={"tenant_id": "t1"})
    s.upsert([{**r, "lang": "en"} for r in records("d1", 2, "t1")])
    assert s.count() == 2


def test_pgvector_never_changes_app_schema(env):
    if env.kind != "pgvector":
        pytest.skip("pgvector only")
    import psycopg

    def snapshot():
        with psycopg.connect(env.dsn) as c:
            cols = c.execute("SELECT column_name, data_type, udt_name, is_nullable, column_default FROM information_schema.columns "
                             "WHERE table_name = %s ORDER BY ordinal_position", (env.name,)).fetchall()
            idx = c.execute("SELECT indexdef FROM pg_indexes WHERE tablename = %s ORDER BY 1", (env.name,)).fetchall()
            typmod = c.execute("SELECT atttypmod FROM pg_attribute WHERE attrelid = %s::regclass AND attname = 'embedding'",
                               (env.name,)).fetchone()
            return cols, idx, typmod

    before = snapshot()
    assert env.sink.apply_schema() == {"skipped": "pgvector tables are owned by the App"}
    env.sink.upsert(records("d1", 3))
    env.sink.upsert(records("d1", 2))
    env.sink.delete("d1")
    env.sink.upsert(records("d2", 1))
    assert snapshot() == before
    with psycopg.connect(env.dsn) as c:
        extra = c.execute(f"SELECT metadata FROM {env.name}").fetchone()[0]
    assert extra["chunk_strategy"] == "section" and extra["doc_type"] == "manual"  # unknown keys -> metadata jsonb


def test_pgvector_drops_extras_without_metadata_column(pg_dsn):
    import psycopg

    t = f"folio_it_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(pg_dsn) as c:
        c.execute(f"CREATE TABLE {t} (doc_id text, chunk_id text, body text, embedding vector(8))")
    try:
        s = create_sink({"type": "pgvector", "table": t, "dsn": pg_dsn, "columns": {"content": "body"}})
        s.upsert(records("d1", 2))
        assert s.count("d1") == 2 and s.expected_dimension() == 8
        with psycopg.connect(pg_dsn) as c:
            assert c.execute(f"SELECT body FROM {t} ORDER BY chunk_id").fetchone()[0].startswith("kubernetes")
    finally:
        with psycopg.connect(pg_dsn) as c:
            c.execute(f"DROP TABLE {t}")
