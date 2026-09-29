# Homeowners Underwriting Agent

A local FastAPI service that runs a five-agent underwriting pipeline as a LangGraph
`StateGraph`. Intake reads structured JSON or raw ACORD text (through an LLM with Groq,
Gemini and OpenAI fallback). Enrichment fans out in parallel with `Send`, and the hazard
lookup uses live FEMA National Risk Index data through the Apify MCP server. Risk assessment
runs deterministic rules, then an LLM review that can add cited flags and recommend a
decision inside code-enforced guardrails. Loss history, property data and the premium are
still mocked.

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

## Risk assessment and triage (Phase 4)

The deterministic rules always run first and their flags always stand. With an LLM
configured (and `LLM_RISK_ASSESSMENT=true`, the default), the LLM then reviews the
submission, the enrichment data and the rule flags, and returns a memo, extra flags, an
overall risk score and a recommendation with a confidence.

**Guardrails, enforced in code:**

- The LLM may raise only the judgment rules in the catalog (`rules.CATALOG`, `UW-101` to
  `UW-105`), and every flag must cite a `source_field` that exists in the input. Anything
  else is dropped and listed in the memo's `rejected_flags`. Severity comes from the
  catalog, not the LLM.
- Hard stops are code only. The LLM can never decline, and can never approve past a hard
  stop or a caution flag.
- The LLM can approve only when no rule fired and its confidence is at least
  `LLM_APPROVE_MIN_CONFIDENCE` (0.7); otherwise the submission is referred. A "decline"
  recommendation becomes a referral. So the LLM can make a decision more cautious, never less,
  which also bounds what a prompt injection in the data could achieve.
- If the LLM fails or returns invalid JSON, the rules-only result is used and the memo says so.
- The audit record's `model_version` names the risk model, e.g. `risk=groq/openai/gpt-oss-120b`.
- LLM calls retry rate limits and overloads (429/5xx) with backoff, honouring `Retry-After`,
  before falling back to the next provider.

| Rule | Raised by | Meaning |
| --- | --- | --- |
| HS-001 | code | Roof older than 25 years (hard stop) |
| SR-010 / SR-011 / SR-012 | code | Pool / 2+ claims / trampoline |
| HZ-001 / HZ-002 | code | Composite hazard 70+ / hazard data unavailable |
| UW-101 | LLM | Coverage A implausible for the property |
| UW-102 | LLM | Effective date in the past or over 12 months out |
| UW-103 | LLM | A single peril Very High though the composite is below the cutoff |
| UW-104 | LLM | Within 1 mile of the coast |
| UW-105 | LLM | Internally inconsistent data (roof older than house, future years) |

## Evaluation set

`src/underwriting_agent/evals/cases.json` holds 20 synthetic submissions labelled with the
decision a careful underwriter would make: 14 the rules decide, and 6 "judgment" cases the
rules cannot see. Hazard data is fixed per case, so no Apify calls are made.

```bash
uv run python -m underwriting_agent.evals          # rules only (free)
uv run python -m underwriting_agent.evals --llm    # rules + LLM review (uses your LLM keys)
```

| Run | Accuracy | Judgment cases | Unsafe approvals |
| --- | --- | --- | --- |
| Rules only | 14/20 | 0/6 | 6 |
| Rules + LLM (Groq `openai/gpt-oss-120b`, 2026-09-29) | 20/20 | 6/6 | 0 |

"Unsafe approvals" counts cases approved that should have been referred or declined. The
labels are synthetic judgments; review them before relying on the scores, and add cases as
you find disagreements.

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
uv run pytest        # 94 tests; fakes only, never calls an LLM, the geocoder or Apify
uv run ruff check .  # lint
uv run ruff format . # format
```

## Layout

```
src/underwriting_agent/
  api/            FastAPI app (main.py) and routes
  agents/         intake, enrichment, risk_assessment (rules + LLM review), triage, compliance
  evals/          labelled evaluation set and scorecard runner
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
- **LLM review is additive.** It adds cited judgment flags and recommendations on top of the
  rules; the rules alone still decide every hard stop.
- **Data:** use synthetic applicants only.
- **Timestamps** are UTC.
- **Database:** `underwriting.db` (SQLite) is created in the working directory on first run.
  Delete it to reset.

## Roadmap

1. Phase 1: schemas, API, SQLite, stubbed agents
2. Phase 2: Groq/Gemini/OpenAI client with fallback; Intake reads raw ACORD text
3. Phase 3: LangGraph `StateGraph` with `Send` fan-out; live Apify hazard MCP
4. Phase 4 (this): LLM risk assessment and triage with citations, guardrails and an eval set
5. Decision log hardening and an evaluation set
6. Provider comparison
7. React underwriter workbench (CORS for `localhost:5173` is already enabled)
