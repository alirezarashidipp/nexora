# Project State

Last updated: 2026-11-23

## Status

The desktop workspace supports **Ask Once → Discover → Choose → Investigate → Verify → Continue**.
Every discovery asks the language model to rank the ten metadata entries. Up to three
cards explain exact or related coverage. A configured link opens the dashboard and
Answer Here retains the original question. The router opens no SQLite connections.
Keyword fallback applies only when the language service is unavailable. Invalid model
rankings and valid empty results have distinct states.

Answer Here routes every question through model-based source selection and planning.
Suggested questions use the same path as typed questions. The planner can choose a
defined business metric or compose a scoped query over individual fields. A clarification
can resolve a missing baseline or an unsupported request. SQLite supplies the displayed
rows, SQL and bound parameters. The five synthetic databases cover London, Manchester,
Dubai, Mumbai, Bengaluru and Shanghai, with ten tables and at most 20 rows each.

The AG-UI LangGraph adapter streams node progress and shared UI snapshots from the same
checkpointed graph that holds conversation memory. One bounded checkpoint per thread
retains business context. A server restart clears it. Dashboard destinations remain
placeholders unless configured.

Regression tests use the OpenAI SDK with a simulated HTTP provider. They exercise
paraphrases, novel field-level questions, comparisons, incremental schema loading, and
step delivery over an actual HTTP stream while the simulated provider is still waiting.
This verifies integration and evidence on the fixtures. Live provider relevance and
planning quality have not been measured on this branch.

On 23 November 2026 the full suite passed 60 Python tests. The database self-check,
JavaScript checks and Compose validation also passed. A desktop Chrome walkthrough
used a simulated provider and the real application to verify recommendation reasons,
configured navigation, missing links, Answer Here after a reload, and a two-city
comparison whose chart matched the SQL evidence. It also exercised clarification.

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
- Neither workspace imposes a result-row cap. The employee workspace sends query
  rows to the answer model. The conversation workspace formats evidence directly
  from SQLite and keeps rows outside conversation memory.
- The frontend targets desktop widths of 1180px and above; it has no mobile
  layout or dark appearance.
- Charts require 2-20 rows with one unique text-label column and one numeric
  column whose values are non-negative and not all zero. Pie requires 2-8
  strictly positive values.
- The employee workflow makes three model calls for a valid question. Conversation
  discovery makes one model call. Answer Here and follow-ups use a source-selection
  call followed by a scoped planning call when a source can be chosen.
- Custom conversation plans support column selection, filtering, grouping, common
  aggregates and ranking. Requests that require unsupported calculations or
  unavailable data prompt a clarification.
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
