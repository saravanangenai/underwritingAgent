from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./underwriting.db"
    cors_origins: list[str] = [
        "http://localhost:5173"
    ]  # Vite's default port, for the React app later

    # LLM providers. Any provider without a key is skipped; the rest are tried in this order.
    llm_providers: list[str] = ["groq", "gemini", "openai"]
    llm_timeout_seconds: float = 60.0
    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"
    gemini_model: str = "gemini-flash-latest"
    openai_model: str = "gpt-5.4-mini"
