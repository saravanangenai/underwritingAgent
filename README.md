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

### Start everything on Windows

`scripts/dev.ps1` starts the API and the workbench in two windows, waits until both answer,
and opens the workbench in your browser (add `-NoBrowser` to skip that). It checks that `uv`
and `npm` are installed and that ports 8000 and 5173 are free, and runs `npm install` on the
first run.

```bash
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

Close the two server windows to stop.

## Underwriter workbench (Phase 7)

A Vue 3 + Vite + TypeScript app in `frontend/` for the people who act on referrals. Start the
API as above, then in a second terminal:

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

- **Queue** (`/`): submissions newest first, filterable by status (the filter and page are in
  the URL), with address, status, non-info flags, confidence and which model assessed each.
- **Submission** (`/submissions/:id`): the decision and rationale; every flag with its
  severity, whether a rule or the LLM raised it and the field it cites; the LLM memo and
  recommendation; rejected LLM flags; FEMA hazard data with top perils; and the audit record
  (model and rules versions, prompt versions, hash chain).
- **New submission** (`/new`): a structured form or pasted ACORD text. Missing fields come back
  as a list; a decided submission opens its detail page.
- **Audit banner**: every page checks `/audit/verify` and turns red if the chain is broken.

The workbench reads `GET /submissions` (added in this phase: `status`, `limit` up to 200 and
`offset`). Point it at another API with `VITE_API_URL` in `frontend/.env.local`; the API must
list the workbench's origin in `CORS_ORIGINS`. The toolchain is pinned to versions that run on
Node 18 (Vite 5, Vitest 2); on Node 20.19+ it can move to Vite 8.

```bash
npm test             # component and API-client tests (the API is mocked)
npm run build        # type-check with vue-tsc, then a production build in dist/
```

## Deploying (Phase 10)

The app runs on one Google Cloud free-tier **e2-micro** VM (1 GB RAM) without Docker: the API as
a sandboxed systemd service (one uvicorn worker, capped at 384 MB), Caddy serving the workbench
and proxying `/api` with automatic HTTPS (capped at 128 MB), SQLite on the VM's disk with
nightly backups, and a 1 GB swap file as a safety net. Measured: the API idles at about
121 MB and peaked at 138 MB under load; CI re-checks this on every change
(`deploy/memcheck.py`).

```bash
uv run python deploy/package.py      # build the release bundle on your computer
# copy the bundle and deploy/install.sh to the VM, then on the VM:
sudo bash install.sh <bundle>.tar.gz --email you@example.com
sudo uw-users create --username you --name "Your Name" --role admin
```

Step-by-step instructions (creating the VM, keys, updates, rollback, backups and restore) are
in [deploy/README.md](deploy/README.md).

## Sign-in and roles (Phase 9)

Everything except `GET /health` requires signing in. Users live in the application database;
passwords are hashed with scrypt from the Python standard library.

| Role | Can |
| --- | --- |
| viewer | read the queue, submissions, decisions, history and audit status |
| underwriter | also submit cases and review referrals |
| admin | also manage users |

**First admin** (once per database; the password is typed at a hidden prompt, 12+ characters):

```bash
uv run python -m underwriting_agent.auth create --username ada --name "Ada L" --role admin
```

After that, admins add users on the workbench's **Users** page (or with the same command). Other
commands: `list`, `set-password --username X`, `deactivate --username X`; `--password-stdin`
reads the password from a pipe for scripts.

- **Browsers** get an httpOnly, SameSite=Lax session cookie from `POST /auth/login`. **API
  clients** use the returned `token` as `Authorization: Bearer <token>` (the Postman collection
  signs in first and does this for you).
- Sessions last `SESSION_HOURS` (12). Five failed sign-ins lock a username for five minutes.
  Unknown usernames and wrong passwords get the same answer and take the same time.
- Changing a role, deactivating a user or resetting their password signs them out everywhere.
  The last active admin cannot be deactivated or demoted.
- Reviews are recorded as the signed-in user, `Display Name (username)`; a `reviewer` field in
  the request is ignored. Submissions record `submitted_by`.
- The workbench calls the API at `/api` on its own origin: Vite proxies it to
  `http://127.0.0.1:8000` in development (`API_PROXY_TARGET` changes that), and the production
  web server will do the same. Same origin keeps the cookie simple and first-party.
- In production set `COOKIE_SECURE=true` so the cookie is only sent over HTTPS.
- Existing databases gain the `users` and `sessions` tables and `submissions.submitted_by` in
  place on startup.

## Underwriter review (Phase 8)

A referred submission waits for a person. On its detail page the workbench shows a review panel:
approve, request information or decline, with a note (at least 10 characters) and the
reviewer's name. The API behind it:

```bash
curl -X POST localhost:8000/submissions/SUB-1234/review -H 'content-type: application/json'   -d '{"action": "approve", "note": "Pool fence confirmed by photos.", "reviewer": "A. Underwriter"}'
curl -H "Authorization: Bearer $TOKEN" localhost:8000/submissions/SUB-1234/history
```

- Only `referred` submissions can be reviewed (409 otherwise, including a second review).
- The review is **appended** to the hash-chained log as a new entry with the action
  (`underwriter_approve`, `underwriter_decline` or `underwriter_request_info`), the note, the
  reviewer, the time and `supersedes_entry_hash` pointing at the automated decision. That
  decision stays in the chain unchanged. The new entry carries the inputs and risk memo forward,
  so the latest entry for a submission is self-contained.
- The submission moves to `completed`, `declined` or `needs_more_info`. Only an approval keeps
  the premium quote.
- `GET /submissions/{id}/history` returns every entry, oldest first; the workbench shows it as a
  timeline, and the queue shows who reviewed each case.
- The reviewer is the signed-in user (Phase 9).
- Existing Phase 5+ databases gain the `review_json` column in place on startup. Entries without
  a review hash exactly as before, so existing chains still verify.

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
| UW-101 | LLM | Coverage A below $100,000 or above $5,000,000, or clearly contradicting other data |
| UW-102 | LLM | Effective date in the past or over 12 months out |
| UW-103 | LLM | A single peril Very High though the composite is below the cutoff |
| UW-104 | LLM | Within 1 mile of the coast |
| UW-105 | LLM | Internally inconsistent data (roof older than house, future years) |

## Evaluation set

`src/underwriting_agent/evals/cases.json` holds 38 synthetic submissions labelled with the
decision a careful underwriter would make: 24 the rules decide (including boundaries such as
a roof exactly 25 years old and a hazard score of exactly 70), and 14 "judgment" cases the
rules cannot see. Ten judgment cases should be referred; four should be approved, to catch an
over-cautious LLM. Hazard data is fixed per case, so no Apify calls are made. Year and date
fields are templates (`{year-24}`, `{date+60}`) resolved against today, so labels stay true.

```bash
uv run python -m underwriting_agent.evals                   # rules only (free)
uv run python -m underwriting_agent.evals --llm             # rules + LLM review
uv run python -m underwriting_agent.evals --llm --repeat 3  # also measure consistency
uv run python -m underwriting_agent.evals --llm --save      # scorecard to eval-results/
uv run python -m underwriting_agent.evals --gate            # exit 1 on a regression
```

Results on 2026-09-29 (LLM runs used Groq `openai/gpt-oss-120b`, with Gemini as fallback):

| Run | Accuracy | Judgment cases | Unsafe approvals | Over-referrals |
| --- | --- | --- | --- | --- |
| Rules only | 28/38 | 4/14 | 10 | 0 |
| Rules + LLM, run 1 | 38/38 | 14/14 | 0 | 0 |
| Rules + LLM, run 2 | 38/38 | 14/14 | 0 | 0 |
| Rules + LLM, run 3 | 35/38 | 14/14 | 0 | 3 |

- **Unsafe approvals** are cases approved that should have been referred or declined: 0 in all
  114 LLM decisions.
- **Consistency:** 35 of 38 cases got the same decision in all three runs. A3, A6 and B1 were
  referred once each under UW-101 (implausible Coverage A); with no square footage in the
  input, the LLM's view of a plausible limit varied. UW-101 was then bounded to Coverage A
  below $100,000 or above $5,000,000 (rules `2026-09-phase5`). The next run scored 38/38 with
  A3, A6 and B1 approved; runs 2 and 3 were stopped when the free-tier quotas ran out, so
  re-run `--repeat 3` to confirm stability.
- **Free-tier limits:** Groq allows 8,000 tokens per minute (about 3-4 risk reviews a minute)
  and Gemini's free quota ran out after a day of evaluation runs, so large runs are slow and
  may fall back to rules. Fallbacks are counted in `llm_fallbacks`.
- **The gate** fails rules-only runs on any wrong rules case (that is what CI runs), and LLM
  runs on any unsafe approval or missed decline. Over-referrals are reported, not failed,
  because they add caution rather than risk.
- The scorecard for these runs is in `eval-results/`. The labels are synthetic judgments;
  review them before relying on the scores, and add cases as you find disagreements.

## Provider comparison (Phase 6)

Run the same cases through each provider **alone** (no fallback) and compare accuracy, safety,
speed and token use side by side:

```bash
uv run python -m underwriting_agent.evals --compare groq,gemini --pace 20 --save
uv run python -m underwriting_agent.evals --provider gemini          # one provider only
```

- Every LLM reply now records `latency_seconds` (including retry waits), prompt and completion
  tokens as reported by the provider, and the number of retries.
- The summary adds `median_latency_s`, `p90_latency_s`, `avg_tokens` and `retries`, plus
  `llm_answered`, `accuracy_answered` and `unsafe_answered`. A case the provider could not
  answer (rate limit, outage) falls back to the rules and is marked `n/a`, so outages are
  reported as availability, not scored as the provider's mistakes.
- `--pace` waits between cases so free-tier rate limits do not skew the comparison. Groq's free
  tier allows 8,000 tokens per minute and one review uses about 1,400-2,000, so use 20 seconds;
  a full 38-case comparison of two providers then takes about 30 minutes.
- Choose the winner with `LLM_PROVIDERS` in `.env`, fastest safe provider first.

First measurement (2026-09-29, both free tiers nearly exhausted after a day of evaluations):
Groq answered 1 of 4 cases in 2.0 s using 1,418 tokens; Gemini answered none (daily quota
used up). Re-run the full comparison after the quotas reset (Groq requests hourly, Gemini
daily at midnight Pacific).

## Audit log (Phase 5)

Every decision is appended to `decision_log` with everything needed to replay and explain it:

- `inputs`: the submission and enrichment exactly as the agents saw them.
- `prompt_versions`: short hashes of the ACORD and risk-review system prompts in force.
- `llm_review_raw`: the LLM's risk reply before validation (so rejected flags can be audited).
- `model_version` and `rules_version`, as before.

The log is **hash chained**. Each entry's `entry_hash` is a SHA-256 over its content and the
previous entry's hash (`prev_hash`; the first entry points at 64 zeros). Changing, removing or
reordering any entry breaks the chain from that point on:

```bash
curl localhost:8000/audit/verify
# {"ok": true, "entries_checked": 42, "first_bad_entry_id": null, "reason": null}
```

This makes tampering **detectable**, not impossible: someone with write access could rebuild
the whole chain. For stronger guarantees, periodically record the latest `entry_hash`
somewhere the database admins cannot change (a signed log, a ticket, object-lock storage).

A database from before Phase 5 has no hash columns. On startup its `decision_log` table is
renamed to `decision_log_legacy_<timestamp>` (nothing is deleted) and a new chained log starts.

## CI

`.github/workflows/ci.yml` runs on every push to `main` and every pull request: `ruff check`,
`ruff format --check`, the test suite, and the rules-only evaluation gate
(`python -m underwriting_agent.evals --gate`), which fails if any rules case regresses. It needs
no secrets: tests use fakes, and the gate uses no LLM or Apify.

## LLM providers

Intake sends `{"acord_text": "..."}` payloads to an LLM, which extracts the same nested
fields the JSON samples use. Structured JSON never touches the LLM.

- **Every value is backed by a quote.** For each field it fills in, the model also returns the
  exact words it read and a confidence. Code checks that the quote appears in the application
  text. A value whose quote is not in the text, or that was read below `INTAKE_MIN_CONFIDENCE`
  (0.6, in `rules.py`), is dropped: a required field then returns "needs more info" with the
  reason, and an optional one stays unknown. A value with no quote is kept but marked.
  The evidence is stored in the audit record (`inputs.intake.evidence`) and shown on the
  submission page under "How the application was read".
- With `OPENAI_ENABLED=true` (the default) OpenAI is tried first; with `false` it is skipped and
  Groq goes first. Any provider with a key in `.env` (`GROQ_API_KEY`, `GEMINI_API_KEY`,
  `OPENAI_API_KEY`) is used, in `LLM_PROVIDERS` order after that; a timeout, error status or
  non-JSON reply moves on to the next one.
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
| GET | `/submissions` | Queue: newest first, `status` filter, `limit`/`offset` paging |
| GET | `/submissions/{id}` | Stored submission and current status |
| GET | `/submissions/{id}/decision` | Audit record for the decision |
| POST | `/auth/login`, `/auth/logout` | Sign in (cookie + token) / sign out |
| GET | `/auth/me` | The signed-in user |
| GET, POST, PATCH | `/users`, `/users/{username}` | Manage users (admin) |
| POST | `/submissions/{id}/review` | Underwriter decision on a referred submission |
| GET | `/submissions/{id}/history` | Every audit entry for a submission, oldest first |
| GET | `/audit/verify` | Recompute the decision log's hash chain |
| GET | `/health` | Liveness check |

## Develop

```bash
uv run pytest        # 181 tests; fakes only, never calls an LLM, the geocoder or Apify
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
frontend/         Vue 3 underwriter workbench (see above)
  schemas/        Pydantic models shared across agents and the API
  persistence/    SQLAlchemy async models and repository (SQLite)
  samples/        synthetic submissions
  rules.py        guideline thresholds and version stamps
deploy/            production deployment: install.sh, systemd units, Caddyfile, backups,
                  package.py (release bundle), memcheck.py (memory budget)
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
4. Phase 4: LLM risk assessment and triage with citations, guardrails and an eval set
5. Phase 5: hash-chained, replayable decision log; CI; larger eval set with consistency runs
6. Phase 6: provider comparison with latency, tokens, pacing and availability
7. Phase 7: Vue underwriter workbench (Vue 3 + Vite + TypeScript in `frontend/`): queue,
   submission detail with cited flags and audit record, new-submission form, audit banner;
   `GET /submissions` list endpoint
8. Phase 8: underwriter review of referrals (approve, decline, request info) with
   notes, appended to the audit chain; submission history
9. Phase 9: sign-in, sessions and roles (viewer, underwriter, admin); users page;
   reviews and submissions attributed to the signed-in user
10. Phase 10 (this): deploy to a Google Cloud free-tier e2-micro without Docker (systemd, Caddy
    with HTTPS, nightly backups, release bundles, 1 GB memory budget checked in CI)
