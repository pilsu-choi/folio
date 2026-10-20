
import pytest

from folio.errors import ContractError, DimensionMismatchError
from folio.pipeline import delete_document, load_pipeline, reprocess, run_pipeline
from tests.conftest import CALLS, FAIL_PAGES, SINKS


def test_all_stages_replaceable_and_layout(make_cfg, sample, tmp_path):
    cfg = make_cfg(map="tests.conftest:tag_map")
    res = run_pipeline(sample, cfg)
    assert res.status == "succeeded"
    assert res.counts == {"pages": 1, "blocks": 3, "chunks": 3}
    assert set(res.timings) == {"parse", "chunk", "map", "embed", "index"}
    assert res.calls["structure"] == 1
    rows = SINKS["default"]
    assert len(rows) == 3 and all(r["tag"] == "x" and len(r["embedding"]) == 4 for r in rows.values())
    files = sorted(k.split("/", 2)[2] for k in __import__("folio.storage", fromlist=["x"]).open_storage(cfg.storage).list("idx/"))
    assert files == ["chunks.json", "document.json", "manifest.json", "source.pdf"]
    st = __import__("folio.storage", fromlist=["x"]).open_storage(cfg.storage)
    chunks = st.get_json(f"idx/{res.doc_id}/chunks.json")
    assert "tag" not in chunks[0]  # chunks.json is pre-map
    assert st.get_json(f"idx/{res.doc_id}/manifest.json")["pipeline"] == "t"


@pytest.mark.parametrize("stage,expect", [
    ("parse", {"parse"}), ("chunk", {"parse", "chunk"}), ("map", {"parse", "chunk", "map"}),
    ("embed", {"parse", "chunk", "map", "embed"}),
])
def test_stop_after(make_cfg, sample, stage, expect):
    res = run_pipeline(sample, make_cfg(), stop_after=stage)
    assert set(res.timings) == expect
    assert SINKS.get("default", {}) == {}


def test_storage_env_override(make_cfg, sample, tmp_path, monkeypatch):
    other = tmp_path / "other"
    monkeypatch.setenv("FOLIO_STORAGE_URI", str(other))
    run_pipeline(sample, make_cfg(), stop_after="parse")
    assert (other / "idx").is_dir()


def test_mapper_deleting_required_field_fails_clearly(make_cfg, sample):
    with pytest.raises(ContractError) as e:
        run_pipeline(sample, make_cfg(map="tests.conftest:bad_map"))
    assert "tests.conftest:bad_map" in str(e.value) and "content" in str(e.value)
    assert SINKS.get("default", {}) == {}


def test_dimension_mismatch_before_upsert(make_cfg, sample):
    cfg = make_cfg(sink={"fn": "tests.conftest:memory_sink", "index": "idx", "dim": 8})
    with pytest.raises(DimensionMismatchError):
        run_pipeline(sample, cfg)
    assert SINKS.get("default", {}) == {}


def test_embed_null_and_contextualized(make_cfg, sample):
    run_pipeline(sample, make_cfg(embed=None))
    assert all("embedding" not in r for r in SINKS["default"].values())


def test_idempotent_rerun(make_cfg, sample):
    cfg = make_cfg()
    a = run_pipeline(sample, cfg, source_uri="upload://t/doc.pdf")
    ids = sorted(SINKS["default"])
    b = run_pipeline(sample, cfg, source_uri="upload://t/doc.pdf")
    assert a.doc_id == b.doc_id and sorted(SINKS["default"]) == ids and len(ids) == 3


def test_partial_status(make_cfg, sample):
    FAIL_PAGES["on"] = True
    res = run_pipeline(sample, make_cfg())
    assert res.status == "partial" and res.errors[0]["page"] == 2
    assert len(SINKS["default"]) == 3


def test_reprocess_from_map_makes_zero_parse_calls(make_cfg, sample):
    cfg = make_cfg()
    res = run_pipeline(sample, cfg)
    assert CALLS["parse"] == 1 and CALLS["chunk"] == 1
    SINKS["default"].clear()
    out = reprocess(make_cfg(map="tests.conftest:tag_map"), "map")
    assert CALLS["parse"] == 1 and CALLS["chunk"] == 1
    assert [o.doc_id for o in out] == [res.doc_id] and len(SINKS["default"]) == 3
    assert all(r["tag"] == "x" for r in SINKS["default"].values())
    assert res.calls["structure"] == 1 and out[0].calls["structure"] == 0


def test_reprocess_from_chunk_and_parse(make_cfg, sample):
    res = run_pipeline(sample, make_cfg(), source_uri="upload://t/doc.pdf")
    reprocess(make_cfg(), "chunk", [res.doc_id])
    assert CALLS["parse"] == 1 and CALLS["chunk"] == 2
    out = reprocess(make_cfg(), "parse")
    assert CALLS["parse"] == 2 and out[0].doc_id == res.doc_id


def test_delete_document(make_cfg, sample):
    cfg = make_cfg()
    res = run_pipeline(sample, cfg)
    delete_document(cfg, res.doc_id)
    assert SINKS["default"] == {}
    assert reprocess(cfg, "map") == []


def test_load_pipeline_by_name(tmp_path, monkeypatch):
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "pipelines" / "p.yaml").write_text(
        "sink: {fn: 'tests.conftest:memory_sink'}\nembed: null\n", encoding="utf-8")
    cfg = load_pipeline("p", str(tmp_path))
    assert cfg.name == "p" and cfg.embed is None
    monkeypatch.setenv("FOLIO_CONFIG_DIR", str(tmp_path))
    assert load_pipeline("p").config_dir == str(tmp_path.resolve())


def test_config_dir_on_sys_path_for_mappers(make_cfg, sample, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "my_mappers_xyz.py").write_text("def m(doc, chunk):\n    return {**chunk, 'z': 1}\n")
    run_pipeline(sample, make_cfg(map="my_mappers_xyz:m", config_dir=str(d)))
    assert all(r["z"] == 1 for r in SINKS["default"].values())


def test_converted_pdf_is_stored_next_to_source(make_cfg, tmp_path):
    from folio.contracts import content_hash
    from folio.storage import open_storage
    from tests.test_convert import office_zip

    src = tmp_path / "a.docx"
    src.write_bytes(office_zip("word/document.xml"))
    parse = {"fn": "folio.parse:default", "converter": "tests.test_convert:fake_converter",
             "backend": "tests._parse_helpers:FakeBackend", "vlm": False}
    cfg = make_cfg(parse=parse, chunk="folio.chunk:adaptive")
    res = run_pipeline(str(src), cfg, stop_after="parse")
    st = open_storage(cfg.storage)
    base = f"idx/{res.doc_id}"
    pdf = st.get_bytes(f"{base}/converted.pdf")
    conv = st.get_json(f"{base}/document.json")["meta"]["convert"]
    assert conv["pdf_hash"] == content_hash(pdf) and conv["pdf_key"] == "converted.pdf"
    assert st.get_json(f"{base}/manifest.json")["converted_pdf"] == "converted.pdf"
    # reprocessing later stages keeps the PDF the stored bbox values refer to
    reprocess(cfg, "chunk")
    assert st.exists(f"{base}/converted.pdf")
