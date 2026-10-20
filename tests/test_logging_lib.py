import json

import httpx
import pytest

from folio import logging as flog
from folio.contracts import Document
from folio.embed.http import post_json
from folio.errors import FolioError
from folio.eval import testset
from folio.generation import http as chat


@pytest.fixture
def lines(capfd):
    def read():
        return [json.loads(x) for x in capfd.readouterr().err.splitlines() if x.startswith("{")]
    yield read
    flog.configure("INFO", force=True)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(request):
    return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                                     "usage": {"total_tokens": 3}})


def test_debug_only_at_debug(lines):
    flog.configure("INFO", force=True)
    chat([{"role": "user", "content": "q"}], url="http://x", transport=httpx.MockTransport(_ok))
    assert not [r for r in lines() if r["level"] == "DEBUG"]
    flog.configure("DEBUG", force=True)
    chat([{"role": "user", "content": "q"}], url="http://x", transport=httpx.MockTransport(_ok))
    ev = {r["event"] for r in lines()}
    assert {"http_request", "http_response", "llm_reply"} <= ev


def test_post_json_retry_warns(lines):
    flog.configure("INFO", force=True)
    calls = []

    def h(request):
        calls.append(1)
        return httpx.Response(503 if len(calls) == 1 else 200, json={"ok": 1})

    assert post_json(_client(h), "http://x/e", {"a": 1}, 3, 0) == {"ok": 1}
    warn = [r for r in lines() if r["level"] == "WARNING"]
    assert len(warn) == 1 and warn[0]["attempt"] == 1 and warn[0]["of"] == 3 and warn[0]["status"] == 503


def test_post_json_4xx_logged(lines):
    flog.configure("INFO", force=True)
    with pytest.raises(httpx.HTTPStatusError):
        post_json(_client(lambda r: httpx.Response(429, text="slow down")), "http://x/e", {}, 3, 0)
    err = [r for r in lines() if r["level"] == "ERROR"]
    assert err and err[0]["status"] == 429 and "slow down" in err[0]["body"]


def test_no_choices_raises(lines):
    flog.configure("INFO", force=True)
    from folio.errors import FolioError

    with pytest.raises(FolioError, match="choices"):
        chat([], url="http://x", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"error": "x"})))
    rows = lines()
    assert any(r["level"] == "ERROR" for r in rows), rows


def test_secret_not_logged(capfd):
    flog.configure("DEBUG", force=True)
    try:
        chat([{"role": "user", "content": "q"}], url="http://u:pw@x", api_key="sk-test-SECRET123",
             transport=httpx.MockTransport(_ok))
        assert "SECRET123" not in capfd.readouterr().err
    finally:
        flog.configure("INFO", force=True)


def _doc():
    from folio.contracts import Block, Page

    text = "이 문서는 로깅 테스트를 위한 충분히 긴 문장입니다. " * 20
    return Document(doc_id="d1", source_uri="file:///x.pdf", content_hash="sha256:0", mime_type="application/pdf", pages=[Page(page=1, blocks=[Block(type="paragraph", text=text, text_source="native")])])


@pytest.mark.parametrize("reply,reason", [("not json", "no JSON array"), ('[{"type": "fact", "question": "q"', "JSON decode"),
                                           ('[{"type": "fact", "question": ]', "JSON decode")])
def test_testset_unit_failed(lines, monkeypatch, reply, reason):
    flog.configure("DEBUG", force=True)
    monkeypatch.setattr(testset, "chat", lambda *a, **k: reply)
    with pytest.raises(FolioError, match="every LLM call failed"):
        testset.generate([_doc()], pages=1, pairs=0, types=("fact",), llm_url="http://x", workers=1, min_chars=10)
    rows = lines()
    failed = [r for r in rows if r["event"] == "testset_unit_failed"]
    assert len(failed) == 2 and failed[0]["attempt"] == 1 and failed[0]["doc_ids"] == ["d1"]
    assert reason in failed[0]["error"]
    assert any(r["event"] == "testset_unit_reply" for r in rows)


def test_testset_http_error_counts_failed(lines, monkeypatch):
    flog.configure("INFO", force=True)
    def boom(*a, **k):
        raise httpx.HTTPStatusError("429", request=httpx.Request("POST", "http://x"), response=httpx.Response(429))

    monkeypatch.setattr(testset, "chat", boom)
    with pytest.raises(Exception, match="every LLM call failed"):
        testset.generate([_doc()], pages=1, pairs=0, types=("fact",), llm_url="http://x", workers=1, min_chars=10)
    assert next(r for r in lines() if r["event"] == "testset_unit_failed")["error_class"] == "HTTPStatusError"


def test_parse_items_missing_outer_bracket():
    reply = 'ok: [{"type": "comparison", "question": "q", "evidence": [{"page": "A", "quote": "x"}]}'
    assert testset._parse_items(reply)[0]["question"] == "q"
    assert testset._parse_items('<think>[x]</think>[{"a": 1}] trailing') == [{"a": 1}]
