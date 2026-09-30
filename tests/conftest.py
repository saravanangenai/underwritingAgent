import asyncio

import pytest
from fastapi.testclient import TestClient

from tests.fakes import ACORD_EXTRACTION, FakeLLM
from underwriting_agent.api.main import create_app
from underwriting_agent.auth import store
from underwriting_agent.auth.schemas import UserCreate
from underwriting_agent.config import Settings
from underwriting_agent.persistence.db import create_engine, create_session_factory, init_db

# Test-only credentials for the seeded users below.
PASSWORD = "test-password-123"
USERS = {
    "ada": ("admin", "Ada Admin"),
    "uma": ("underwriter", "Uma Underwriter"),
    "vic": ("viewer", "Vic Viewer"),
}


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
        llm_risk_assessment=False,  # tests that need it pass a risk LLM explicitly
    )


async def _seed(database_url: str) -> None:
    engine = create_engine(database_url)
    await init_db(engine)
    async with create_session_factory(engine)() as session:
        for username, (role, name) in USERS.items():
            await store.create_user(
                session,
                UserCreate(username=username, display_name=name, role=role, password=PASSWORD),
            )
    await engine.dispose()


@pytest.fixture
def users(settings):
    """Seed an admin (ada), an underwriter (uma) and a viewer (vic)."""
    asyncio.run(_seed(settings.database_url))
    return USERS


def sign_in(client: TestClient, username: str = "uma") -> TestClient:
    """Sign `client` in as `username` with a bearer token and return it."""
    response = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    client.cookies.clear()  # use the token only, so tests see exactly one credential
    return client


@pytest.fixture
def client(settings, users):
    """An API client signed in as the underwriter uma."""
    with TestClient(create_app(settings)) as test_client:
        yield sign_in(test_client)


@pytest.fixture
def anon_client(settings, users):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def llm_client(settings, users):
    with TestClient(create_app(settings, llm=FakeLLM(ACORD_EXTRACTION))) as test_client:
        yield sign_in(test_client)
