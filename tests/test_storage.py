from folio.storage import LocalStorage, open_storage, read_uri


def test_local_roundtrip_and_uri_forms(tmp_path):
    st = open_storage(f"file://{tmp_path}/s")
    assert isinstance(st, LocalStorage)
    st.ensure()
    st.ensure()  # idempotent
    st.put_bytes("a/b/x.bin", b"hi")
    st.put_json("a/c.json", {"k": "한글"})
    assert st.get_bytes("a/b/x.bin") == b"hi"
    assert st.get_json("a/c.json") == {"k": "한글"}
    assert st.exists("a/c.json") and not st.exists("zz")
    assert st.list("a/") == ["a/b/x.bin", "a/c.json"]
    st.delete("a/b")
    assert st.list("a/") == ["a/c.json"]
    assert read_uri(st.uri("a/c.json")).startswith(b"{")
    assert read_uri("file://" + st.uri("a/c.json")).startswith(b"{")


def test_s3_uri_parsing(monkeypatch):
    boto3 = __import__("pytest").importorskip("boto3")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "a")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "b")
    st = open_storage("s3://bkt/pre/fix")
    assert (st.bucket, st.prefix) == ("bkt", "pre/fix")
    assert st.uri("k") == "s3://bkt/pre/fix/k"
