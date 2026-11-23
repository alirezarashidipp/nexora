# Nexora

Open the desktop app at <http://127.0.0.1:8000> for dashboard discovery and
threaded analysis. The employee workspace is available at `/employees`.

## Try a conversation

With a configured OpenAI or Azure OpenAI provider:

1. Ask **How many active employees are in Bengaluru?**
2. Select **Workforce footprint** and choose **Answer Here**. The synthetic data contains one active employee in Bengaluru.
3. Ask **Compare it with London.** The same metric gives Bengaluru 1 and London 2.
4. Expand **SQL and evidence** to inspect the query, parameters and rows.

Dashboard discovery sends the question and ten compact dashboard descriptions to the model.
The workspace labels the result **Semantic match** or **Keyword fallback**. A provider outage
still allows keyword discovery. Answer Here and follow-ups need a working model, including
when using a suggested question. A missing dashboard match still allows analysis of the data.

Each new conversation gets its own thread. One server process holds up to 64 conversations
in RAM. Restarting it clears their memory.

The catalogue contains ten fictional dashboards across five SQLite databases.
For the exact schema, link configuration and reproducible build see
[the database guide](docs/database.md). For the streaming contract see
[the conversation API](docs/api.md#post-ag-ui).

## Employee SQL assistant

Ask a question in plain English, turn it into guarded SQLite, and return an
answer grounded in the query result.

This schema-guided SQL application keeps the full path visible: FastAPI
receives the question, LangGraph validates and completes it, OpenAI or Azure OpenAI produces structured
output, SQLite runs a read-only query, and a small HTML/CSS/JavaScript frontend
shows the result.

## What it does

1. Checks whether a question is valid, incomplete, or outside the table scope.
2. Normalizes clear spelling mistakes and shorthand using the live schema.
3. Generates one SQLite `SELECT` query.
4. Applies application and SQLite-level read-only guardrails.
5. Answers from the returned rows only.
6. Automatically renders a Bar chart for compatible results and offers Pie when
   supported.

Try questions such as:

- `How many coders?`
- `How many employees are in each department?`
- `Data engineers in MRM`
- `How many?` — asks for the missing detail instead of guessing.

## Quick start

Requirements:

- Python 3.14 with `pip`
- An OpenAI API key or Azure OpenAI access through Microsoft Entra for general language interpretation and the employee assistant

```powershell
# Use Python available on that computer:
& "D:\path\to\python.exe" -m venv .venv

.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Add the API key to .env
.\.venv\Scripts\python.exe main.py
```

Open <http://127.0.0.1:8000>. Interactive API documentation is available at
<http://127.0.0.1:8000/docs>.

## Docker

Create `.env` as shown above, then run:

```powershell
docker compose up --build
```

Open <http://127.0.0.1:8000>. The image includes the existing fictional
`main_datawarehouse.db` asset and opens it read-only. `.env` is supplied only at
runtime. The image also includes the five read-only conversation assets.

## Configuration

Local runs load the project-root `.env` using python-dotenv. Existing process
variables take precedence. `.env.local` is no longer loaded; move its settings
into `.env` if upgrading an older checkout. Docker Compose also uses `.env`.

| Setting | Requirement / default |
| --- | --- |
| `LLM_PROVIDER` | `openai` (default) or `azure`; other values are rejected |
| `LLM_TEMPERATURE` | Required, finite number from 0 to 2 |
| `OPENAI_API_KEY` | Required for OpenAI only |
| `OPENAI_MODEL` | Defaults to `gpt-4.1-mini` when omitted |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` | Required for Azure only |
| `AZURE_OPENAI_TOKEN_SCOPE` | Required scope from your Azure environment |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION` | Required values from your Azure environment |
| `AZURE_OPENAI_DEPLOYMENT` | Required Azure deployment name, used as the request model |
| `HTTPS_PROXY`, `NO_PROXY` | Optional corporate proxy settings |
| `DATABASE_PATH` | Required existing SQLite file; example: `main_datawarehouse.db` |

All required values reject blanks. Change provider through configuration and restart.
The selected model/deployment must support Chat Completions, temperature,
`store=False`, and strict JSON Schema. Parameters are never silently removed.
See [LLM connection](docs/llm-connection.md) for lifecycle and verification details.

```powershell
# Explicit LIVE check: sends only synthetic data, may incur provider charges.
.\.venv\Scripts\python.exe -m app.connection_check
```

Client setup and `main.py --check` do not make model requests. The latter is an
offline database self-check, not proof of working provider credentials.

## Request flow

```text
question
  -> load live schema and COLUMN_GUIDE
  -> validate / normalize question
       -> incomplete or invalid: return a short explanation
       -> valid: generate SELECT
  -> validate SQL
  -> run read-only SQLite query
  -> generate grounded answer
  -> inspect returned rows in the browser
       -> compatible two-column result: render Bar and offer valid chart options
```

Prompts live in `prompts/*.yml`; the fixed table name and dataset-specific
column descriptions live in `app/schema.py`. The frontend discovers that
table's live schema through
`GET /schema`, so it does not hardcode employee columns.

## Safety boundary

Generated SQL is untrusted. The employee backend accepts a single `SELECT` against the
fixed `data` table, enables `PRAGMA query_only`, and installs a SQLite authorizer
that denies other tables and write operations. These layers must remain in
place even when prompts improve.

This is currently a local schema-guided SQL application: `/ask` has no
authentication, rate limiting, or query timeout. Do not expose it directly to the public internet.
See [docs/security.md](docs/security.md) for the threat model.

## Project map

```text
.
|-- app/                    # FastAPI, LangGraph, config, and SQLite code
|-- prompts/                # Versioned prompt templates
|-- evals/                  # Opt-in live LLM evaluations
|-- static/                 # HTML, CSS, and JavaScript frontend
|-- tests/                  # Standard-library regression tests
|-- docs/                   # Architecture, API, database, deployment, security
|   `-- adr/                # Architecture decision records
|-- skills/                 # Project-local AI maintenance skill
|-- workflows/              # Human-readable AI workflow contract
|-- .github/workflows/      # Continuous integration
|-- AGENTS.md               # Rules for coding agents
|-- PROJECT_STATE.md        # Current capabilities and limits
|-- Dockerfile
|-- docker-compose.yml
|-- Makefile
|-- csv_to_sqlite.py       # Standalone CSV-to-SQLite data tool
|-- main.py
|-- requirements.txt       # Pinned direct Python dependencies
|-- requirements-eval.txt  # Optional DeepEval dependencies
`-- main_datawarehouse.db   # Tracked prebuilt sample data asset
```

Directories for migrations, benchmarks, memory, or scripts are intentionally
absent until the project has real code that belongs in them.

## Change the data

### Convert a CSV file

Stop the app and create a new database file with the standalone standard-library
tool:

```powershell
.\.venv\Scripts\python.exe csv_to_sqlite.py `
  .\employees.csv `
  .\main_datawarehouse.db `
  --infer-types `
  --replace
```

The first row supplies the column names. Without `--infer-types`, every column
stays `TEXT`, which preserves identifiers such as `00123`. With the flag, only
clear `INTEGER` and `REAL` columns are converted. Blank cells become `NULL` in
both modes.

Use `--delimiter ";"` for semicolon-separated files or `--delimiter tab` for
TSV. An existing output file is refused unless `--replace` is explicit, and
replacement happens only after the new database is complete.

To use another SQLite asset:

1. Create and populate the SQLite file in your separate data process. Its table
   must be named `data`.
2. Set only `DATABASE_PATH` in `.env` if the file path differs.
3. Update `COLUMN_GUIDE`, `EXAMPLE_QUESTIONS`, and `EVAL_CASES` in
   `app/schema.py`.
4. Make every `COLUMN_GUIDE` key exactly match a live table column.
5. Restart the app.

The application never creates, migrates, or seeds the database. See
[docs/database.md](docs/database.md) for the existing-file contract.

## Verify changes

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --check
node --check static/app.js
node --test tests/test_chart.cjs
```

## Run the live LLM evaluations

The eval suite calls the configured application provider and is separate from
deterministic tests and CI. Its DeepEval judge still requires an OpenAI API key;
DeepEval uses `OPENAI_MODEL_NAME` when set, or `gpt-4.1` as the judge model.
When the app uses OpenAI, the judge can reuse its key. When the app uses Azure,
the judge still needs a separate `OPENAI_API_KEY`; the app itself does not.

### 1. Configure and install

Keep the same `OPENAI_API_KEY` used by the app in `.env`. Optionally add
`OPENAI_MODEL_NAME=<judge-model>` there when the default judge is unavailable.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-eval.txt
$env:PYTHONUTF8 = "1"
$env:DEEPEVAL_TELEMETRY_OPT_OUT = "1"  # optional
```

### 2. Run free checks first

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --check
```

### 3. Run the live eval

```powershell
.\.venv\Scripts\deepeval.exe test run evals\test_llm.py -v
```

The three groups mean:

- `test_generated_sql_matches_reference_result`: generated and reference SQL
  may differ as text, but their rows must match on the current database.
- `test_unclear_or_invalid_question_is_rejected_without_sql`: status must be
  `incomplete` or `invalid`, and no SQL may run.
- `test_final_answer_is_correct_relevant_and_grounded`: DeepEval judges answer
  relevance, faithfulness to `rows`, and correctness against the reference.

After changing the database, update `COLUMN_GUIDE`, `EXAMPLE_QUESTIONS`, and
all affected `EVAL_CASES` in `app/schema.py`. In particular, a rejected question
must be changed if the new schema now contains the requested field. The
`answers` rows are fixed fixtures, not rows loaded from the database. Live
metrics cost API calls and may vary slightly; retry one borderline result, but
do not lower the `0.7` thresholds merely to make a failure pass.

For a Persian explanation of the current evaluation scope, metrics, and limits,
see the [evaluation guide](docs/evaluation-guide-fa.md).

## Documentation

- [Architecture](docs/architecture.md)
- [API](docs/api.md)
- [Database and data imports](docs/database.md)
- [Deployment](docs/deployment.md)
- [Security](docs/security.md)
- [Architecture decisions](docs/adr/0001-schema-guided-read-only-sql.md)
- [Contributing](CONTRIBUTING.md)
- [Current project state](PROJECT_STATE.md)
