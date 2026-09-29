# Homeowners Underwriting Agent

A local FastAPI service that runs a five-agent underwriting pipeline as a LangGraph
`StateGraph`. Intake reads structured JSON or raw ACORD text (through an LLM with Groq,
Gemini and OpenAI fallback). Enrichment fans out in parallel with `Send`, and the hazard
lookup uses live FEMA National Risk Index data through the Apify MCP server. Risk assessment
is still rule-based; loss history, property data and the premium are still mocked.

## Run it

```bash
uv sync
cp .env.example .env          # add LLM keys for acord_text and APIFY_TOKEN for live hazard data
uv run uvicorn underwriting_agent.api.main:app --reload
```

Open http://localhost:8000/docs. The `POST /underwrite` request body has a dropdown of five
synthetic samples: **clean**, **high risk**, **decline**, **incomplete** and **acord text**.

```bash
curl -X POST localhost:8000/underwrite -H 'content-type: application/json' \
  -d @src/underwriting_agent/samples/high_risk.json
```

A Postman collection with every sample, the lookups and the error cases (each with test
assertions) is in `postman/underwriting-agent.postman_collection.json`. Import it and run
the folders in order.

## Pipeline

```
START -> intake -- missing fields ----------------------------------> END
           |
           +-- complete: Send x N --> enrich (parallel) --> risk_assessment
                                                                 |
                                END <-- compliance <-- triage <--+
```

`graph/builder.py` builds the graph. Intake's conditional edge either ends the run (missing
fields) or sends one `enrich` task per lookup from `select_lookups`; their results are merged
by a list reducer before risk assessment.

## Hazard data (Apify MCP)

With `APIFY_TOKEN` set, the hazard lookup:

1. Geocodes the property address with the US Census geocoder (free, no key). The match must
   be in the submitted ZIP, because the geocoder otherwise snaps to a nearby street.
2. Calls the `malonestar/fema-nri-county-risk-profile` Actor through `https://mcp.apify.com`
   with the coordinates, at census-tract resolution.
3. Reads the run's dataset row and returns FEMA's composite risk score (a 0-100 national
   percentile) as `composite_score`, plus the rating, top hazards, tract and NRI version.

- **Cost:** one Actor run per submission, about $0.006 on Apify's free tier.
- **Failures** (address not found, Apify down, timeout) never fall back to mock scores. The
  hazard result is marked `unavailable` and rule **HZ-002** refers the submission for manual
  review, so no decision is made on fake hazard data.
- **No token**, or `HAZARD_PROVIDER=mock`, uses the mock. Tests always use the mock or an
  in-process fake MCP server.
- **Privacy:** the property address goes to the Census geocoder and its coordinates to Apify.
- Smoke test (one billed run): `uv run python -m underwriting_agent.tools.hazard_apify`

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

## What each sample returns

With `HAZARD_PROVIDER=mock`:

| Sample | HTTP | Status | Why |
| --- | --- | --- | --- |
| clean | 200 | completed (auto_approve) | No caution or hard-stop flags |
| high_risk | 200 | referred (refer_to_underwriter) | Pool, trampoline, 3 claims, high hazard ZIP |
| decline | 200 | declined (auto_decline) | Roof older than 25 years (HS-001) |
| incomplete | 422 | needs_more_info | `coverage.coverage_a_limit` missing; nothing is enriched |
| acord_text | 200 | referred (refer_to_underwriter) | LLM extracts the text; the pool triggers SR-010 |

With live hazard data, **clean** is referred: its real census tract scores 89.96, above the
HZ-001 cutoff of 70. The other synthetic addresses do not geocode, so they also get HZ-002.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/underwrite` | Run the pipeline, return decision, risk memo and enrichment |
| GET | `/submissions/{id}` | Stored submission and current status |
| GET | `/submissions/{id}/decision` | Audit record for the decision |
| GET | `/health` | Liveness check |

## Develop

```bash
uv run pytest        # 68 tests; fakes only, never calls an LLM, the geocoder or Apify
uv run ruff check .  # lint
uv run ruff format . # format
```

## Layout

```
src/underwriting_agent/
  api/            FastAPI app (main.py) and routes
  agents/         intake, enrichment, risk_assessment, triage, compliance
  llm/            provider client (OpenAI-compatible) and ordered fallback
  tools/          hazard_apify (live FEMA NRI via Apify MCP) and mocked data sources
  graph/          builder.py (LangGraph StateGraph), orchestrator.py runs it
  schemas/        Pydantic models shared across agents and the API
  persistence/    SQLAlchemy async models and repository (SQLite)
  samples/        synthetic submissions
  rules.py        guideline thresholds and version stamps
```

## Notes

- **Still mocked:** loss history, property data and the premium (0.4% of Coverage A).
- **Hazard threshold:** HZ-001 refers scores of 70 or more. With live data that is roughly the
  riskiest 30% of US census tracts, which includes much of California. Tune
  `HAZARD_CAUTION_SCORE` in `rules.py` to your appetite.
- **Risk assessment is a rule-based stub.** Phase 4 replaces it with an LLM that must still
  cite a rule id and source field for every flag.
- **Data:** use synthetic applicants only.
- **Timestamps** are UTC.
- **Database:** `underwriting.db` (SQLite) is created in the working directory on first run.
  Delete it to reset.

## Roadmap

1. Phase 1: schemas, API, SQLite, stubbed agents
2. Phase 2: Groq/Gemini/OpenAI client with fallback; Intake reads raw ACORD text
3. Phase 3 (this): LangGraph `StateGraph` with `Send` fan-out; live Apify hazard MCP
4. LLM risk assessment and triage with citations
5. Decision log hardening and an evaluation set
6. Provider comparison
7. React underwriter workbench (CORS for `localhost:5173` is already enabled)
