# Architecture

## Purpose

The desktop workspace sends a thread UUID and a run UUID to `POST /ag-ui`.
The Python `ag-ui-langgraph` adapter runs the same compiled LangGraph graph that uses
`InMemorySaver`. The `ag-ui-protocol` event encoder streams the adapter's run, node
and shared-state events over SSE. The browser shows progress as nodes run.

1. **Discover dashboards.** The model receives the question and a compact list of dashboard IDs,
   titles, descriptions, keywords and KPI labels. The application validates the ranking. An
   unavailable provider or invalid output uses deterministic keyword ranking. A valid empty
   ranking is a meaningful outcome.
2. **Select sources.** Answer Here restores the original wording. The model sees table topics,
   approved relationship names and business context to choose a base and an ordered join path.
   A dashboard recommendation can guide the choice. A question with no recommendation can
   still use available data.
3. **Plan the query.** Only selected table definitions and join keys enter the planning request.
   Follow-ups carry the recent analyses, existing filters and outstanding clarification.
   Previously used sources supply compact column names and types. Newly required source
   definitions are validated and cached. Both example buttons and typed questions use this path.
4. **Read evidence.** The application builds a bound SELECT for either a defined business
   metric or custom columns, aggregates, filters and sorting. A snapshot carries the answer,
   SQL, bindings and SQLite rows to the browser. Restoring a completed answer reruns its
   validated plan on the immutable fixtures.

Only the selected dashboard is writable client state, on the select action and after
validation against that thread's recommendations. The graph's request context holds
model inputs and transient evidence. The checkpoint keeps six recent questions and
analysis contexts and twelve run IDs for retries. Failure restores the last committed
checkpoint. The demo serialises turns in one process and retains up to 64 threads.

See the [Python AG-UI LangGraph adapter](https://github.com/ag-ui-protocol/ag-ui/blob/main/integrations/langgraph/python/README.md)
and [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence).

## Employee assistant

The application answers natural-language questions against the fixed SQLite
table `data` while keeping schema knowledge, prompts, orchestration, database
access, and presentation separate.

## Components

| Component | Responsibility |
| --- | --- |
| `static/` | Browser interface, result-shape inspection, and native charts |
| `app/api.py` | FastAPI routes, validation, and HTTP error mapping |
| `app/workflow.py` | LangGraph state, nodes, and routing |
| `prompts/` | Versioned question-review, SQL, and answer templates |
| `app/schema.py` | Fixed table name, column guide, examples, and eval cases |
| `app/database.py` | Existing-file validation, schema introspection, and guarded queries |
| `app/config.py` | Application and database settings |
| `app/llm_connection.py` | dotenv, provider validation, SDK lifecycle, Entra tokens, shared requests |

## Request flow

```mermaid
flowchart LR
    UI["Browser UI"] --> API["POST /ask"]
    API --> Schema["Load SQLite schema + COLUMN_GUIDE"]
    Schema --> Review["Review and normalize question"]
    Review -->|"incomplete / invalid"| Reply["Return explanation"]
    Review -->|"valid"| SQL["Generate one SELECT"]
    SQL --> Guard["Validate SQL"]
    Guard --> DB["Query-only SQLite + authorizer"]
    DB --> Answer["Generate grounded answer"]
    Answer --> UI
    UI --> Shape["Inspect returned row shape"]
    Shape -->|"compatible two-column result"| Chart["Render Bar; offer Pie when valid"]
```

The workflow state is a `TypedDict` with three nodes. `review_question` loads
the cached schema and routes non-valid questions to the end. Valid questions
continue through `query_database`, which generates and safely executes SQL,
then `generate_answer`.

## Runtime sources of truth

- Environment values: process environment first, then project-root `.env` via python-dotenv
- Table contract: fixed table `data`, SQLite metadata, and `COLUMN_GUIDE`
- Prompt behavior: `prompts/*.yml`
- Workflow behavior: `app/workflow.py`

The API exposes schema metadata to the frontend, which makes the UI independent
of the current table's column names.

Chart eligibility is deterministic browser logic. It uses returned rows only,
does not add a model call, and leaves the `/ask` response contract unchanged.

## Boundaries

- FastAPI validates the question type and minimum length before the graph runs.
  There is no application maximum length or result-row cap.
- SQLite is opened in URI read-only mode and is never created or seeded here.
- Both providers use Chat Completions with system/user messages and strict JSON
  Schema. Pydantic validates responses in the workflow, outside connection code.
- Each workflow node owns a connection context and closes it after its model call.
  Callers may reuse a client for multiple requests within a context. There is no
  global cached client. Entra token acquisition/refresh uses the SDK callback.
- Generated SQL remains untrusted and passes through database guardrails.
- Each employee `/ask` request is stateless.

See the accepted [architecture decision](adr/0001-schema-guided-read-only-sql.md).
