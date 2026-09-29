import json

import httpx
import pytest

from tests.fakes import FakeLLM
from underwriting_agent.config import Settings
from underwriting_agent.llm import FallbackLLM, LLMError, ProviderClient, build_llm


def _chat_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _client(handler, provider: str = "groq") -> ProviderClient:
    return ProviderClient(
        provider, "test-key", "test-model", transport=httpx.MockTransport(handler)
    )


async def test_provider_sends_json_mode_request_and_parses_reply():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _chat_reply('{"ok": true}')

    reply = await _client(handler, "gemini").complete_json("sys", "user")
    assert reply.data == {"ok": True}
    assert reply.model_id == "gemini/test-model"
    assert seen["url"].endswith("/v1beta/openai/chat/completions")
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]


async def test_provider_strips_code_fence():
    reply = await _client(lambda r: _chat_reply('```json\n{"a": 1}\n```')).complete_json("s", "u")
    assert reply.data == {"a": 1}


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"error": "rate limited"}),
        httpx.Response(500),
        _chat_reply("not json"),
        _chat_reply("[1, 2]"),
        httpx.Response(200, json={"unexpected": True}),
    ],
)
async def test_provider_failures_raise_llm_error(response):
    with pytest.raises(LLMError) as exc:
        await _client(lambda r: response).complete_json("s", "u")
    assert "test-key" not in str(exc.value)


async def test_network_error_raises_llm_error():
    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(LLMError, match="groq: ConnectTimeout"):
        await _client(handler).complete_json("s", "u")


async def test_fallback_uses_next_provider_on_failure():
    first, second = FakeLLM(error="groq: HTTP 429"), FakeLLM({"ok": True})
    reply = await FallbackLLM([first, second]).complete_json("s", "u")
    assert reply.data == {"ok": True}
    assert len(first.calls) == len(second.calls) == 1


async def test_fallback_reports_every_failure():
    llm = FallbackLLM([FakeLLM(error="groq: HTTP 500"), FakeLLM(error="openai: HTTP 401")])
    with pytest.raises(LLMError, match="groq: HTTP 500; openai: HTTP 401"):
        await llm.complete_json("s", "u")


def test_build_llm_keeps_order_and_skips_missing_keys():
    settings = Settings(
        _env_file=None,
        llm_providers=["openai", "groq", "gemini"],
        groq_api_key="g",
        gemini_api_key=None,
        openai_api_key="o",
    )
    llm = build_llm(settings)
    assert [c.provider for c in llm.clients] == ["openai", "groq"]


def test_build_llm_without_keys_is_none():
    settings = Settings(_env_file=None, groq_api_key=None, gemini_api_key=None, openai_api_key=None)
    assert build_llm(settings) is None


def test_build_llm_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        build_llm(Settings(_env_file=None, llm_providers=["claude"]))
