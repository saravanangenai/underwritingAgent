"""LLM access for the agents: one chat client per provider, plus ordered fallback.

Groq, Gemini and OpenAI all expose an OpenAI-compatible /chat/completions endpoint, so one
small httpx client covers all three. FallbackLLM tries them in order and moves on when a
provider is down, rate limited, or returns something that is not a JSON object.
"""

import json
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


class ProviderClient:
    """Calls one provider's OpenAI-compatible chat endpoint in JSON mode."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,  # tests pass a MockTransport
    ):
        self.provider = provider
        self.model = model
        self._url = f"{BASE_URLS[provider]}/chat/completions"
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

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
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
                response = await c.post(self._url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.provider}: {type(exc).__name__}") from exc

        if response.status_code != 200:
            raise LLMError(f"{self.provider}: HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
            data = _parse_json_object(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError(f"{self.provider}: reply was not a JSON object") from exc
        return LLMReply(data=data, provider=self.provider, model=self.model)


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


def build_llm(settings: Settings) -> FallbackLLM | None:
    """A FallbackLLM over every provider in settings.llm_providers that has a key, or None."""
    clients: list[LLM] = []
    for provider in settings.llm_providers:
        if provider not in BASE_URLS:
            raise ValueError(f"Unknown LLM provider {provider!r}; use one of {sorted(BASE_URLS)}")
        key = getattr(settings, f"{provider}_api_key")
        if key is None or not key.get_secret_value():
            continue
        model = getattr(settings, f"{provider}_model")
        clients.append(
            ProviderClient(provider, key.get_secret_value(), model, settings.llm_timeout_seconds)
        )
    return FallbackLLM(clients) if clients else None
