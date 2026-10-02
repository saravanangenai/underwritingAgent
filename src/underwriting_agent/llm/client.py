"""LLM access for the agents: one chat client per provider, plus ordered fallback.

Groq, Gemini and OpenAI all expose an OpenAI-compatible /chat/completions endpoint, so one
small httpx client covers all three. FallbackLLM tries them in order and moves on when a
provider is down, rate limited, or returns something that is not a JSON object.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import structlog

from underwriting_agent.config import Settings

log = structlog.get_logger()

BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openai": "https://api.openai.com/v1",
}


class LLMError(Exception):
    """A provider call failed. Messages never include the API key."""


@dataclass
class LLMReply:
    data: dict[str, Any]
    provider: str
    model: str
    latency_seconds: float | None = None  # wall time for the call, including retry waits
    prompt_tokens: int | None = None  # as reported by the provider
    completion_tokens: int | None = None
    retries: int = 0

    @property
    def model_id(self) -> str:
        return f"{self.provider}/{self.model}"


class LLM(Protocol):
    async def complete_json(self, system: str, user: str) -> LLMReply: ...


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):  # some models wrap JSON in a fence even in JSON mode
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


# Rate limited or temporarily overloaded: worth a short wait and another try.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _retry_delay(response: httpx.Response, attempt: int, base: float, cap: float) -> float:
    """Honour a numeric Retry-After header, else exponential backoff; never more than cap."""
    try:
        wait = float(response.headers.get("retry-after", ""))
    except ValueError:
        wait = base * 2**attempt
    return max(0.0, min(wait, cap))


class ProviderClient:
    """Calls one provider's OpenAI-compatible chat endpoint in JSON mode."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,  # tests pass a MockTransport
        max_retries: int = 2,
        retry_base_seconds: float = 2.0,
        retry_cap_seconds: float = 20.0,
    ):
        self.provider = provider
        self.model = model
        self._url = f"{BASE_URLS[provider]}/chat/completions"
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport
        self._max_retries = max_retries
        self._retry_base = retry_base_seconds
        self._retry_cap = retry_cap_seconds

    async def complete_json(self, system: str, user: str) -> LLMReply:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        started = time.perf_counter()
        retries = 0
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
                for attempt in range(self._max_retries + 1):
                    response = await c.post(self._url, json=payload, headers=headers)
                    if response.status_code not in RETRYABLE_STATUS:
                        break
                    if attempt < self._max_retries:
                        retries += 1
                        wait = _retry_delay(response, attempt, self._retry_base, self._retry_cap)
                        log.info(
                            "llm_retry",
                            provider=self.provider,
                            status=response.status_code,
                            wait_seconds=wait,
                        )
                        await asyncio.sleep(wait)
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.provider}: {type(exc).__name__}") from exc

        if response.status_code != 200:
            raise LLMError(f"{self.provider}: HTTP {response.status_code}")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            data = _parse_json_object(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError(f"{self.provider}: reply was not a JSON object") from exc
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return LLMReply(
            data=data,
            provider=self.provider,
            model=self.model,
            latency_seconds=round(time.perf_counter() - started, 3),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            retries=retries,
        )


class FallbackLLM:
    """Tries each client in order and returns the first successful reply."""

    def __init__(self, clients: list[LLM]):
        if not clients:
            raise ValueError("FallbackLLM needs at least one client")
        self.clients = clients

    async def complete_json(self, system: str, user: str) -> LLMReply:
        errors: list[str] = []
        for client in self.clients:
            try:
                return await client.complete_json(system, user)
            except LLMError as exc:
                log.warning("llm_provider_failed", error=str(exc))
                errors.append(str(exc))
        raise LLMError("All LLM providers failed: " + "; ".join(errors))


def build_provider(settings: Settings, provider: str) -> ProviderClient | None:
    """One provider's client, or None when it has no key. Used alone for fair comparisons."""
    if provider not in BASE_URLS:
        raise ValueError(f"Unknown LLM provider {provider!r}; use one of {sorted(BASE_URLS)}")
    if provider == "openai" and not settings.openai_enabled:
        return None
    key = getattr(settings, f"{provider}_api_key")
    if key is None or not key.get_secret_value():
        return None
    model = getattr(settings, f"{provider}_model")
    return ProviderClient(provider, key.get_secret_value(), model, settings.llm_timeout_seconds)


def build_llm(settings: Settings) -> FallbackLLM | None:
    """A FallbackLLM over every provider in settings.llm_providers that has a key, or None.

    The primary provider goes first: OpenAI when OPENAI_ENABLED is true, otherwise Groq.
    """
    # OPENAI_ENABLED picks the primary: OpenAI when true, Groq when false. The other providers
    # in LLM_PROVIDERS stay behind it as fallbacks.
    primary = "openai" if settings.openai_enabled else "groq"
    providers = [primary] + [p for p in settings.llm_providers if p != primary]
    clients: list[LLM] = [
        client
        for provider in providers
        if (client := build_provider(settings, provider)) is not None
    ]
    return FallbackLLM(clients) if clients else None
