import pytest

from folio.contracts import Block, Document, make_chunk_id, make_doc_id, parse_config, validate_chunk_record
from folio.contracts.__main__ import main as schema_main
from folio.errors import ConfigError, ContractError


def test_schemas_up_to_date():
    assert schema_main(["--check"]) == 0


def test_block_type_rules():
    Block(type="paragraph", text="a", bbox=[0, 0, 1, 1], text_source="pdf_text")
    Block(type="figure", bbox=[0, 0, 1, 1], text_source="pdf_text")
    Block(type="heading", text="h", text_source="native")
    with pytest.raises(ValueError):
        Block(type="table", bbox=[0, 0, 1, 1], text_source="ocr")
    with pytest.raises(ValueError):
        Block(type="kv", key="k", bbox=[0, 0, 1, 1], text_source="ocr")
    with pytest.raises(ValueError):
        Block(type="paragraph", text="x", text_source="pdf_text")


def test_ids_deterministic():
    a = make_doc_id("s3://b/x.pdf", "sha256:1")
    assert a == make_doc_id("s3://b/x.pdf", "sha256:1") != make_doc_id("s3://b/y.pdf", "sha256:1")
    assert make_chunk_id(a, 3, "t") == make_chunk_id(a, 3, "t")
    doc = Document(doc_id=a, source_uri="x", content_hash="h", mime_type="application/pdf")
    assert Document.model_validate_json(doc.model_dump_json()) == doc


def test_chunk_validation_names_fields():
    rec = {"doc_id": "d", "chunk_id": "c", "content": "x", "page": 1, "section_path": [], "source_uri": "u"}
    validate_chunk_record(rec)
    bad = dict(rec)
    del bad["source_uri"]
    with pytest.raises(ContractError, match="source_uri"):
        validate_chunk_record(bad)
    with pytest.raises(ContractError, match="embedding"):
        validate_chunk_record(rec, require_embedding=True)


def test_config_forms(monkeypatch):
    monkeypatch.setenv("IDX", "contracts")
    cfg = parse_config({
        "parse": "folio.parse:default",
        "chunk": {"fn": "folio.chunk:auto", "max_tokens": 512},
        "map": "mappers:map_contract",
        "sink": {"type": "opensearch", "index": "${IDX}"},
        "storage": "${STORE:-/tmp/x}",
    })
    assert cfg.chunk.opts == {"max_tokens": 512}
    assert cfg.index == "contracts" and cfg.storage == "/tmp/x"
    with pytest.raises(ConfigError):
        parse_config({"sink": {"index": "x"}})
    with pytest.raises(ConfigError):
        parse_config({"sink": {"type": "x", "index": "${NOPE_UNSET}"}})
