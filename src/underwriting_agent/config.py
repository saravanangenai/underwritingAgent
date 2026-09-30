from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./underwriting.db"
    cors_origins: list[str] = [
        "http://localhost:5173"
    ]  # Vite's default port, for the Vue workbench

    # Sign-in. Set COOKIE_SECURE=true when the app is served over HTTPS (production).
    session_hours: float = 12.0
    cookie_secure: bool = False
    login_max_failures: int = 5
    login_lockout_seconds: float = 300.0

    # LLM providers. Any provider without a key is skipped; the rest are tried in this order.
    llm_providers: list[str] = ["groq", "gemini", "openai"]
    llm_timeout_seconds: float = 60.0
    # Let the LLM review each risk after the rules run (one extra LLM call per submission).
    llm_risk_assessment: bool = True
    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"
    gemini_model: str = "gemini-flash-latest"
    openai_model: str = "gpt-5.4-mini"

    # Hazard data. "apify" uses FEMA's National Risk Index through the Apify MCP server when
    # APIFY_TOKEN is set, and the mock otherwise. "mock" always uses the mock (no Apify cost).
    hazard_provider: Literal["apify", "mock"] = "apify"
    apify_token: SecretStr | None = None
    apify_mcp_url: str = "https://mcp.apify.com"
    apify_hazard_actor: str = "malonestar/fema-nri-county-risk-profile"
    apify_hazard_resolution: Literal["tract", "county"] = "tract"
    apify_timeout_seconds: float = 120.0
