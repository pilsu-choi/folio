import json

import pytest

from folio.errors import ConfigError
from folio.sinks import create_sink
from folio.sinks.opensearch import OpenSearchSink, diff_properties


class Custom:
    def __init__(self, **kw):
        self.kw = kw


def test_create_sink_custom_and_unknown():
    s = create_sink({"fn": "tests.test_sinks:Custom", "a": 1})
    assert isinstance(s, Custom) and s.kw == {"a": 1}
    with pytest.raises(ConfigError):
        create_sink({"type": "nope"})


def test_diff_properties_added_changed_nested():
    cur = {"a": {"type": "keyword"}, "v": {"type": "knn_vector", "dimension": 4}, "o": {"properties": {"x": {"type": "text"}}}}
    want = {"a": {"type": "keyword"}, "v": {"type": "knn_vector", "dimension": 8}, "b": {"type": "text"},
            "o": {"properties": {"x": {"type": "text"}, "y": {"type": "keyword"}}}}
    added, changed = diff_properties(want, cur)
    assert added == {"b": {"type": "text"}, "o": {"properties": {"y": {"type": "keyword"}}}}
    assert changed == ["v"]


def test_opensearch_expected_dimension_from_schema(tmp_path):
    d = tmp_path / "schemas" / "opensearch"
    d.mkdir(parents=True)
    (d / "ix.json").write_text(json.dumps({"mappings": {"properties": {"embedding": {"type": "knn_vector", "dimension": 12}}}}))
    s = OpenSearchSink("ix", config_dir=str(tmp_path), client=object())
    assert s.expected_dimension() == 12


def test_pgvector_apply_schema_is_noop():
    s = create_sink({"type": "pgvector", "table": "t", "dsn": "postgresql://x"})
    assert s.apply_schema(reindex=True) == {"skipped": "pgvector tables are owned by the App"}
