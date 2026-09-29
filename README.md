# Homeowners Underwriting Agent

A local FastAPI service that runs the full five-agent flow end to end. Phase 2 adds an LLM
client (Groq, Gemini and OpenAI with fallback) so Intake can read raw ACORD application text.
Risk assessment is still rule-based and every data source is still mocked. Later phases swap
in LangGraph, LLM risk assessment and the Apify hazard MCP.

## Run it

```bash
uv sync
cp .env.example .env          # add at least one LLM key to use acord_text
uv run uvicorn underwriting_agent.api.main:app --reload
```

Open http://localhost:8000/docs. The `POST /underwrite` request body has a dropdown of five
synthetic samples: **clean**, **high risk**, **decline**, **incomplete** and **acord text**.

## LLM providers

Intake sends `{"acord_text": "..."}` payloads to an LLM, which extracts the same nested
fields the JSON samples use. Structured JSON never touches the LLM.

- Any provider with a key in `.env` (`GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`) is
  used. They are tried in `LLM_PROVIDERS` order (default groq, gemini, openai); a timeout,
  error status or non-JSON reply moves on to the next one.
- Models default to `openai/gpt-oss-120b`, `gemini-flash-latest` and `gpt-5.4-mini`;
  override with `GROQ_MODEL`, `GEMINI_MODEL` and `OPENAI_MODEL`.
- If every provider fails, or no key is set, `/underwrite` returns 503 for ACORD text.
- The decision log's `model_version` records which provider and model read the text.
- Check your keys with a tiny real prompt per provider:
  `uv run python -m underwriting_agent.llm`

```bash
curl -X POST localhost:8000/underwrite -H 'content-type: application/json' \
  -d @src/underwriting_agent/samples/high_risk.json
```

A Postman collection with every sample, the lookups and the error cases (each with test
assertions) is in `postman/underwriting-agent.postman_collection.json`. Import it and run
the folders in order.

## What each sample returns

| Sample | HTTP | Status | Why |
| --- | --- | --- | --- |
| clean | 200 | completed (auto_approve) | No caution or hard-stop flags |
| high_risk | 200 | referred (refer_to_underwriter) | Pool, trampoline, 3 claims, high hazard ZIP |
| decline | 200 | declined (auto_decline) | Roof older than 25 years (HS-001) |
| incomplete | 422 | needs_more_info | `coverage.coverage_a_limit` missing; nothing is enriched |
| acord_text | 200 | referred (refer_to_underwriter) | LLM extracts the text; the pool triggers SR-010 |

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/underwrite` | Run the pipeline, return decision, risk memo and enrichment |
| GET | `/submissions/{id}` | Stored submission and current status |
| GET | `/submissions/{id}/decision` | Audit record for the decision |
| GET | `/health` | Liveness check |

## Develop

```bash
uv run pytest        # 44 tests; they use a fake LLM and never call a provider
uv run ruff check .  # lint
uv run ruff format . # format
```

## Layout

```
src/underwriting_agent/
  api/            FastAPI app (main.py) and routes
  agents/         intake, enrichment, risk_assessment, triage, compliance
  llm/            provider client (OpenAI-compatible) and ordered fallback
  tools/          mocked data sources (hazard, loss history, property, rating)
  graph/          orchestrator.py runs the agents; state.py holds the result type
  schemas/        Pydantic models shared across agents and the API
  persistence/    SQLAlchemy async models and repository (SQLite)
  samples/        synthetic submissions
  rules.py        guideline thresholds and version stamps
```

## Notes

- **Mocked on purpose:** hazard scores, loss history, property data and the premium
  (0.4% of Coverage A). None of it is real.
- **Risk assessment is a rule-based stub.** Phase 4 replaces it with an LLM that must still
  cite a rule id and source field for every flag.
- **Data:** use synthetic applicants only.
- **Timestamps** are UTC.
- **Database:** `underwriting.db` (SQLite) is created in the working directory on first run.
  Delete it to reset.

## Roadmap

1. Phase 1: schemas, API, SQLite, stubbed agents
2. Phase 2 (this): Groq/Gemini/OpenAI client with fallback; Intake reads raw ACORD text
3. LangGraph `StateGraph` with `Send` fan-out; real Apify hazard MCP
4. LLM risk assessment and triage with citations
5. Decision log hardening and an evaluation set
6. Provider comparison
7. React underwriter workbench (CORS for `localhost:5173` is already enabled)
