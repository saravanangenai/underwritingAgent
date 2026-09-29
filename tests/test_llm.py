import json

import httpx
import pytest

from tests.fakes import FakeLLM
from underwriting_agent.config import Settings
from underwriting_agent.llm import FallbackLLM, LLMError, ProviderClient, build_llm


def _chat_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _client(handler, provider: str = "groq", max_retries: int = 2) -> ProviderClient:
    return ProviderClient(
        provider,
        "test-key",
        "test-model",
        transport=httpx.MockTransport(handler),
        max_retries=max_retries,
        retry_base_seconds=0,  # no real waiting in tests
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


async def test_rate_limit_is_retried_then_succeeds():
    replies = iter([httpx.Response(429), httpx.Response(503), _chat_reply('{"ok": true}')])
    calls = []

    def handler(request):
        calls.append(request)
        return next(replies)

    reply = await _client(handler).complete_json("s", "u")
    assert reply.data == {"ok": True}
    assert len(calls) == 3


async def test_retries_are_bounded():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    with pytest.raises(LLMError, match="HTTP 429"):
        await _client(handler, max_retries=2).complete_json("s", "u")
    assert len(calls) == 3  # first try + 2 retries


async def test_auth_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401)

    with pytest.raises(LLMError, match="HTTP 401"):
        await _client(handler).complete_json("s", "u")
    assert len(calls) == 1


def test_retry_delay_honours_retry_after_with_a_cap():
    from underwriting_agent.llm.client import _retry_delay

    assert _retry_delay(httpx.Response(429, headers={"retry-after": "3"}), 0, 2, 20) == 3
    assert _retry_delay(httpx.Response(429, headers={"retry-after": "999"}), 0, 2, 20) == 20
    assert _retry_delay(httpx.Response(429), 2, 2, 20) == 8  # 2 * 2**2


async def test_reply_carries_latency_tokens_and_retries():
    replies = iter(
        [
            httpx.Response(429),
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": '{"ok": true}'}}],
                    "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
                },
            ),
        ]
    )
    reply = await _client(lambda r: next(replies)).complete_json("s", "u")
    assert (reply.prompt_tokens, reply.completion_tokens, reply.retries) == (1200, 300, 1)
    assert reply.latency_seconds is not None and reply.latency_seconds >= 0


async def test_missing_usage_is_tolerated():
    reply = await _client(lambda r: _chat_reply('{"ok": true}')).complete_json("s", "u")
    assert reply.prompt_tokens is None and reply.completion_tokens is None


def test_build_provider_returns_one_client_or_none():
    from underwriting_agent.llm import build_provider

    settings = Settings(_env_file=None, groq_api_key="g", gemini_api_key=None)
    assert build_provider(settings, "groq").provider == "groq"
    assert build_provider(settings, "gemini") is None
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        build_provider(settings, "claude")
