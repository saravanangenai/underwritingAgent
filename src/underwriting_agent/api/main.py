from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from underwriting_agent.agents.enrichment import HazardProvider
from underwriting_agent.api.auth_routes import router as auth_router
from underwriting_agent.api.routes import router
from underwriting_agent.auth.deps import LoginThrottle
from underwriting_agent.config import Settings
from underwriting_agent.llm import LLM, build_llm
from underwriting_agent.logging_config import configure_logging
from underwriting_agent.persistence.db import create_engine, create_session_factory, init_db
from underwriting_agent.tools.hazard_apify import build_hazard_provider

log = structlog.get_logger()


def create_app(
    settings: Settings | None = None,
    llm: LLM | None = None,
    hazard: HazardProvider | None = None,
) -> FastAPI:
    """Build the app. `llm` and `hazard` override what settings would build (tests pass fakes)."""
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
        version="0.10.0",
        description=(
            "Phase 5: hash-chained, replayable audit log. Rules plus an LLM risk review with "
            "cited flags and guardrailed triage. "
            "LangGraph pipeline with parallel enrichment, live FEMA National Risk Index hazard "
            "data through the Apify MCP server, and LLM intake of raw ACORD text. Loss history "
            "and property data are mocked."
        ),
        lifespan=lifespan,
    )
    app.state.llm = llm if llm is not None else build_llm(settings)
    app.state.risk_llm = app.state.llm if settings.llm_risk_assessment else None
    app.state.hazard = hazard if hazard is not None else build_hazard_provider(settings)
    log.info(
        "app_configured",
        llm=app.state.llm is not None,
        risk_llm=app.state.risk_llm is not None,
        hazard="apify" if app.state.hazard is not None else "mock",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.login_throttle = LoginThrottle(
        settings.login_max_failures, settings.login_lockout_seconds
    )
    app.include_router(auth_router)
    app.include_router(router)
    return app


app = create_app()
