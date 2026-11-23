# Project State

Last updated: 2026-11-22

## Status

The desktop landing page now supports **Ask Once → Discover → Answer Here →
Continue**. The catalogue has ten dashboards and five prebuilt fictional databases,
with eight columns per table and up to 20 rows per table. One bounded LangGraph
checkpoint per conversation holds the original question or its private reference,
recent questions, dashboard selection, filters and source names. AG-UI streams
the current view, SQL and evidence to the browser.

The four suggested question patterns and city comparisons use the real SQLite
assets offline. A versioned language-planning prompt handles other wording using
catalogue measures. Unavailable measures and ambiguous requests need clarification.
Dashboard links are placeholders until configured. The initial release uses one
server process, with restart and least-recently-used eviction clearing memory.

On 2026-11-22, 46 Python tests passed, including reproducible fixture contents,
exact schema contracts, thread isolation, bounded checkpoints and evidence
queries. A desktop Chrome walkthrough covered dashboard selection, Answer Here,
the Bristol/London comparison, reload, a fresh thread and a three-database
weather query. General language interpretation was exercised with mocked model
responses. The JavaScript checks, database self-check and Compose configuration passed.

The schema-guided SQL application is runnable locally and through Docker. A
user can ask about the fixed SQLite table `data`, receive validation or clarification, inspect
the generated SQL, and get an answer grounded in the database result.

## Working capabilities

- FastAPI serves the web UI and JSON API.
- Local settings load from project-root `.env` using python-dotenv;
  existing process environment values take priority.
- A three-node LangGraph routes valid, incomplete, and invalid questions.
- OpenAI and Azure OpenAI use the official SDK with Chat Completions.
- Provider selection is configuration-only; temperature is explicit and validated.
- Connection code separates client creation, request preparation, and SDK calls
  into plain functions in the same module.
- Azure uses Microsoft Entra EnvironmentCredential with a refreshing token callback.
- Context-managed clients/credentials close on success and error; no global cache.
- Strict JSON output is validated by the workflow before SQL execution.
- An opt-in `python -m app.connection_check` sends only synthetic data.
- SDK timeout is 60 seconds with one retry; provider errors are safely mapped.
- YAML files hold all LLM prompt text.
- A pre-provisioned SQLite file is opened read-only and introspected with
  `COLUMN_GUIDE`.
- SQL has application validation, query-only mode, and table-scoped
  authorization.
- The frontend uses a light-only, desktop HSBC Insight Studio layout with a
  transparent faceted ruby-crystal HSBC hero illustration, architectural
  backdrop, and system fonts.
- The frontend renders expandable field descriptions and four suggested
  question cards from `EXAMPLE_QUESTIONS` through `GET /schema`.
- A View SQL action opens the generated query and raw rows after an answer.
- Workspace and field search filter the live schema and suggested questions;
  fields support grid/list views and insertion into the question.
- Recent insights shows three explicitly labelled, static demo examples. It
  does not store or display real question history.
- The frontend automatically renders a native Bar chart after compatible
  two-column results and offers Pie when supported.
- Standard-library tests cover API/schema contracts, workflow routing,
  database guardrails, result comparison, answer inputs, and chart eligibility.
- An opt-in DeepEval suite measures live question classification, generated SQL
  result equivalence, and final-answer correctness and grounding.
- A standalone standard-library tool converts one flat CSV into a replacement
  SQLite asset without changing the application's read-only runtime.
- Python dependencies install with standard `pip` into a local `.venv`.
- Docker and Compose include the existing fictional SQLite asset.
- GitHub Actions is configured to run deterministic checks without calling
  OpenAI.

## Employee data contract

- Database asset: `main_datawarehouse.db`
- Fixed table: `data`
- Columns: `EMPLOYEE_ID`, `EMPLOYEE_NAME`, `DEPARTMENT`, `STATUS`, `TITLE`
- Rows: 100 fictional sample employees

`main_datawarehouse.db` is intentionally tracked because it contains demo data only.
The application and container never create, migrate, or seed it.

## Deliberate limits

- The employee workspace exposes only the fixed SQLite table `data`.
- There is no migration system; schema changes require database recreation or
  an externally managed database.
- Each `/ask` request is independent. The separate `/ag-ui` route maintains thread memory.
- No application maximum question length or result-row cap is imposed. All query
  rows go to the answer model; provider context limits still apply.
- The frontend targets desktop widths of 1180px and above; it has no mobile
  layout or dark appearance.
- Charts require 2-20 rows with one unique text-label column and one numeric
  column whose values are non-negative and not all zero. Pie requires 2-8
  strictly positive values.
- Valid questions make three model calls; incomplete or invalid questions make
  one.
- Live evals use the selected application provider plus an OpenAI DeepEval judge, can vary between runs, and are
  intentionally excluded from deterministic CI.

## Not production-ready

- No authentication or authorization
- No rate limiting or usage quotas
- No whole-workflow deadline or SQLite execution timeout
- No operational tracing

Do not expose the service publicly until these controls match the deployment
environment.

## Next useful work

1. Add request IDs, safe operational logging, and a whole-workflow deadline.
2. Add authentication and rate limits only when a real deployment requires
   public or shared access.

## LLM verification limits

Provider configuration, request parameters, resource cleanup, and error handling
are covered offline with mocked SDK calls. No successful live OpenAI or Azure
request has been verified for this change. Real credentials and deployment values
must be supplied before running the explicit synthetic connection check.
Models/deployments must support temperature and strict JSON Schema; unsupported
parameters fail visibly and are not removed. `.env.local` is no longer loaded.

Offline verification on 2026-11-17: 36 Python tests, the database self-check,
JavaScript syntax check, chart test, and Docker Compose configuration check passed.
The SDK transport tests use synthetic in-memory HTTP responses, not live services.
