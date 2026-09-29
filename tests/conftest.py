import pytest
from fastapi.testclient import TestClient

from tests.fakes import ACORD_EXTRACTION, FakeLLM
from underwriting_agent.api.main import create_app
from underwriting_agent.config import Settings


@pytest.fixture
def settings(tmp_path):
    # Ignore .env and any key env vars so tests never call a real LLM or Apify.
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        groq_api_key=None,
        gemini_api_key=None,
        openai_api_key=None,
        apify_token=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def llm_client(settings):
    with TestClient(create_app(settings, llm=FakeLLM(ACORD_EXTRACTION))) as test_client:
        yield test_client
