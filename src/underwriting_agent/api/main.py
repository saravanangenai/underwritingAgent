from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from underwriting_agent.api.routes import router
from underwriting_agent.config import Settings
from underwriting_agent.llm import LLM, build_llm
from underwriting_agent.logging_config import configure_logging
from underwriting_agent.persistence.db import create_engine, create_session_factory, init_db


def create_app(settings: Settings | None = None, llm: LLM | None = None) -> FastAPI:
    """Build the app. `llm` overrides the providers built from settings (tests pass a fake)."""
    settings = settings or Settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_engine(settings.database_url)
        await init_db(engine)
        app.state.session_factory = create_session_factory(engine)
        yield
        await engine.dispose()

    app = FastAPI(
        title="Homeowners Underwriting Agent",
        version="0.2.0",
        description=(
            "Phase 2: LLM intake of raw ACORD text with Groq/Gemini/OpenAI fallback. "
            "Risk assessment is still rule-based and data sources are still mocked."
        ),
        lifespan=lifespan,
    )
    app.state.llm = llm if llm is not None else build_llm(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
