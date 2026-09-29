"""Smoke-test each configured provider: `uv run python -m underwriting_agent.llm`.

Sends one tiny JSON-mode prompt per provider (a fraction of a cent) and prints ok or the
error. Key values are never printed.
"""

import asyncio

from underwriting_agent.config import Settings
from underwriting_agent.llm.client import FallbackLLM, LLMError, build_llm


async def main() -> int:
    llm = build_llm(Settings())
    if llm is None:
        print("No LLM keys found. Set GROQ_API_KEY, GEMINI_API_KEY or OPENAI_API_KEY in .env.")
        return 1
    assert isinstance(llm, FallbackLLM)
    failures = 0
    for client in llm.clients:
        try:
            reply = await client.complete_json(
                'Reply with the JSON object {"ok": true} and nothing else.', "ping"
            )
            print(f"{reply.model_id}: ok {reply.data}")
        except LLMError as exc:
            failures += 1
            print(f"FAILED {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
