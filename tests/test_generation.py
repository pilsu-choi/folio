import httpx
import pytest

from folio.contracts import parse_config
from folio.errors import ConfigError, FolioError, GenerationError
from folio.generation import answer, check_messages, http
from folio.retrieval.base import Hit

CHATS: list[list[dict]] = []
QUERIES: list[str] = []


def fake_chat(messages, **opts):
    """Rewrite prompts get a fixed query; answers cite [1] and an out-of-range [9]."""
    CHATS.append(messages)
    if messages[0]["role"] == "user":
        return '"제2조 해지 사유"'
    return opts.get("reply", "해지 사유는 제2조에 있다 [1]. 근거 없음 [9].")


def broken_chat(messages, **opts):
    raise RuntimeError("llm down")


HITS = [Hit("c1", "d1", 1.0, "제2조 해지 사유", {"page": 2, "section_path": ["제2조"]}),
        Hit("c2", "d1", 0.5, "제3조 " + "길다 " * 50, {})]


@pytest.fixture
def cfg(monkeypatch):
    CHATS.clear()
    QUERIES.clear()

    def fake_search(cfg, query, k=None, filters=None, mode=None):
        if "tenant_id" in cfg.retrieval.required_filters and "tenant_id" not in (filters or {}):
            raise ValueError("missing required filters: ['tenant_id']")
        QUERIES.append(query)
        return HITS

    monkeypatch.setattr("folio.pipeline.search", fake_search)

    def make(**generation):
        return parse_config({"sink": {"fn": "x:y"}, "generation": {"chat": "tests.test_generation:fake_chat",
                                                                    **generation}})

    return make


def test_first_turn_skips_rewrite_and_maps_citations(cfg):
    c = cfg()
    res = answer(c, [{"role": "user", "content": "해지 사유는?"}])
    assert len(CHATS) == 1 and QUERIES == ["해지 사유는?"] and res.query == "해지 사유는?"
    system = CHATS[0][0]["content"]
    assert "[1] 제2조 · p.2\n제2조 해지 사유" in system and "[2]\n제3조" in system
    assert res.citations == [{"n": 1, "chunk_id": "c1", "doc_id": "d1"}]  # [9] is out of range
    assert [h.chunk_id for h in res.hits] == ["c1", "c2"]


def test_follow_up_rewrites_query_and_strips_old_citations(cfg):
    c = cfg()
    msgs = [{"role": "user", "content": "계약 해지?"}, {"role": "assistant", "content": "제2조를 보라 [1][2]."},
            {"role": "user", "content": "그 사유는?"}]
    res = answer(c, msgs)
    rewrite, final = CHATS
    assert "Last question: 그 사유는?" in rewrite[0]["content"] and "assistant: 제2조를 보라 ." in rewrite[0]["content"]
    assert QUERIES == ["제2조 해지 사유"] and res.query == "제2조 해지 사유"
    assert [m["role"] for m in final] == ["system", "user", "assistant", "user"]
    assert final[2]["content"] == "제2조를 보라 ." and final[3]["content"] == "그 사유는?"
    cfg_off = cfg(rewrite=False)
    CHATS.clear()
    assert answer(cfg_off, msgs).query == "그 사유는?" and len(CHATS) == 1


def test_budgets_trim_history_and_passages(cfg):
    c = cfg(max_history_tokens=5, max_context_tokens=5)
    msgs = [{"role": "user", "content": "오래된 질문 " * 20}, {"role": "assistant", "content": "답"},
            {"role": "user", "content": "짧은"}, {"role": "assistant", "content": "답변"},
            {"role": "user", "content": "지금 질문"}]
    res = answer(c, msgs)
    final = CHATS[-1]
    assert [m["content"] for m in final[1:]] == ["짧은", "답변", "지금 질문"]  # oldest dropped, starts with user
    assert [h.chunk_id for h in res.hits] == ["c1"]  # top hit kept, second over budget
    CHATS.clear()
    res = answer(cfg(max_history_tokens=0), [{"role": "user", "content": "계약 해지?"},
                                             {"role": "assistant", "content": "[1]"}, {"role": "user", "content": "그 사유는?"}])
    rewrite, final = CHATS
    assert "user: 계약 해지?" in rewrite[0]["content"] and res.query == "제2조 해지 사유"
    assert [m["role"] for m in final] == ["system", "user"]


def test_errors(cfg):
    with pytest.raises(ConfigError, match="no generation"):
        answer(parse_config({"sink": {"fn": "x:y"}}), [{"role": "user", "content": "q"}])
    for bad in ([], [{"role": "system", "content": "x"}, {"role": "user", "content": "q"}],
                [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
                [{"role": "user", "content": " "}]):
        with pytest.raises(ValueError):
            check_messages(bad)
    with pytest.raises(GenerationError, match="llm down"):
        answer(cfg(chat="tests.test_generation:broken_chat"), [{"role": "user", "content": "q"}])
    c = cfg()
    c.retrieval.required_filters = ["tenant_id"]
    with pytest.raises(ValueError, match="tenant_id"):
        answer(c, [{"role": "user", "content": "q"}])


def test_generation_config_toggle():
    assert parse_config({"sink": {"fn": "x:y"}}).generation is None
    on = parse_config({"sink": {"fn": "x:y"}, "generation": True}).generation
    assert on.chat.fn == "folio.generation:http" and on.rewrite
    chat = parse_config({"sink": {"fn": "x:y"}, "generation": {"chat": {"fn": "m:f", "model": "q"}}}).generation.chat
    assert chat.fn == "m:f" and chat.opts == {"model": "q"}
    with pytest.raises(ConfigError):
        parse_config({"sink": {"fn": "x:y"}, "generation": {"nope": 1}})


def test_http_chat_client(monkeypatch):
    seen = {}

    def handler(req):
        seen["url"], seen["auth"] = str(req.url), req.headers.get("authorization")
        seen["body"] = __import__("json").loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "답"}}]})

    out = http([{"role": "user", "content": "q"}], url="http://llm/v1/", model="m", api_key="k",
               transport=httpx.MockTransport(handler))
    assert out == "답" and seen["url"] == "http://llm/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "m" and seen["body"]["messages"][0]["content"] == "q"
    monkeypatch.delenv("FOLIO_LLM_URL", raising=False)
    with pytest.raises(FolioError, match="FOLIO_LLM_URL"):
        http([{"role": "user", "content": "q"}])
    fail = httpx.MockTransport(lambda req: httpx.Response(503))
    with pytest.raises(FolioError, match="generation.http: failed after 2 tries"):
        http([{"role": "user", "content": "q"}], url="http://llm/v1", retries=2, backoff=0, transport=fail)
