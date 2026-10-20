import json

import pytest

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx2")

from folio.errors import FolioError
from folio.generation.anthropic import chat


def _sse(text: str, stop_reason: str = "end_turn", stop_details=None) -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {
            "stop_reason": stop_reason, "stop_sequence": None, "stop_details": stop_details},
            "usage": {"output_tokens": 3}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def _client(seen: list, **sse):
    def handler(req):
        seen.append((req.headers, json.loads(req.content)))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(**sse))

    return anthropic.Anthropic(api_key="k", http_client=anthropic.DefaultHttpxClient(
        transport=httpx.MockTransport(handler)))


def test_system_lifted_and_text_returned(monkeypatch):
    monkeypatch.delenv("FOLIO_LLM_MODEL", raising=False)
    seen = []
    out = chat([{"role": "system", "content": "S1"}, {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q2"}],
               effort="low", client=_client(seen, text="답 [1]"))
    assert out == "답 [1]"
    headers, body = seen[0]
    assert body["system"] == "S1" and body["model"] == "claude-opus-5-5"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["output_config"] == {"effort": "low"} and body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]


def test_no_fallbacks_and_env_model(monkeypatch):
    monkeypatch.setenv("FOLIO_LLM_MODEL", "claude-sonnet-5-5")
    seen = []
    chat([{"role": "user", "content": "q"}], fallbacks=False, client=_client(seen, text="x"))
    headers, body = seen[0]
    assert body["model"] == "claude-sonnet-5-5" and "fallbacks" not in body and "system" not in body
    assert "anthropic-beta" not in headers


def test_refusal_raises():
    with pytest.raises(FolioError, match="declined"):
        chat([{"role": "user", "content": "q"}], client=_client(
            [], text="", stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber",
                                                                "explanation": None}))
