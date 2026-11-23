# Security

## Trust model

User questions and all model output are untrusted. This schema-guided SQL
application is designed for local use with sample data, not for public access
or sensitive employee records.

## Existing controls

1. FastAPI requires a string question with at least two characters.
2. Structured model output constrains validation status and SQL shape.
3. `validate_sql()` accepts one comment-free `SELECT`, and execution requires
   a real read from the fixed `data` table rather than a table name inside text.
4. The existing SQLite file is opened with URI `mode=ro` and
   `PRAGMA query_only = ON`.
5. A SQLite authorizer denies reads from other tables and all SQLite write
   actions.
6. Both providers use `store=False`; raw provider errors are not returned to clients.
7. `.env.local`, `.env`, and virtual environments are excluded from Git and Docker.
8. Docker runs the application as a non-root user.

Prompt rules are helpful but are not a security boundary. Do not remove the
independent SQL and SQLite controls.

## Conversation workspace

Discovery sends the question and public business metadata to the provider, including
published KPIs, views and office coverage. It never reads a SQLite file or sends SQL,
source definitions, result rows or destination URLs. It verifies each dashboard ID and
supporting KPI against the catalogue and checks that the coverage classification agrees
with the stated gap. Only bounded business-safe reasons enter memory. For private
wording the server describes the model-selected KPI and gap using catalogue terms.
For analysis the server validates the selected base table and every step of a reviewed
many-to-one join path before loading its schemas. Both keys are required for the
city-and-date weather join.

Defined metrics compile from reviewed expressions. For custom analyses the model
chooses fields, aggregate operations, filter operators and sort positions. The
compiler checks each identifier against the scoped tables and binds filter values
and any requested limit. The model cannot submit executable SQL or its own join text.
Each fixed asset opens with URI `mode=ro` and `PRAGMA query_only=ON`. The SQLite
authorizer checks database, table, column and allowed functions. The legacy fixed-table
validator and authorizer continue to guard the employee path.

Only short questions consisting of the public demo vocabulary and calendar dates
are retained verbatim. Other original wording stays out of checkpoints. The server
encrypts it with a process-local Fernet key and binds it to the thread. Tab session
storage holds the opaque token, which lets Answer Here recover the exact wording
after a reload. The server verifies the thread and a keyed fingerprint. A new
conversation clears the tab's token. Restarting the process clears its encryption
key and the checkpointer. While a clarification is open, the page
can echo its prior question on the next turn. The server accepts it only if its
keyed fingerprint matches that thread's pending question. The graph stores bounded business
context. Raw rows, SQL, schemas, unvalidated provider replies and unrestricted free
text stay outside checkpoints. General phrasing is processed by the configured provider.
The tab token uses [Fernet authenticated encryption](https://cryptography.io/en/latest/fernet/).

Thread UUIDs identify conversations. They do not authenticate users. Serve this
single-process demo on localhost with synthetic data.

## Secrets

- Store `OPENAI_API_KEY` and `AZURE_CLIENT_SECRET` only in ignored `.env` files or the
  deployment secret store.
- Never paste keys into issues, logs, screenshots, prompts, or committed files.
- Revoke a key immediately if it is exposed.
- `.env.example` must contain names and safe defaults only.

## Known gaps

- `/ask` and `/schema` have no authentication.
- There is no rate limiting, usage quota, or cost ceiling.
- There is no application question-length ceiling or result-row cap; large results
  are loaded in memory and sent to the answer model. Provider context limits still apply.
- SDK requests use a 60-second timeout and at most one retry. This is not a
  whole-workflow deadline; SQLite still has no execution timeout.
- There is no request-level audit trail or security monitoring.
- The returned SQL and rows are visible in the frontend debug panel.

Bind to localhost unless a trusted gateway supplies the missing controls.

## Reporting

Do not include secrets or real employee data in a report. For this small
schema-guided SQL application, open a private report with the repository owner
before publishing a security issue.

## LLM authentication and diagnostics

Azure uses `EnvironmentCredential` and `get_bearer_token_provider`; tokens are
acquired lazily and refreshed by the library. Clients and credentials close on
context exit, including failed calls. Authentication errors are mapped to safe
application messages. SDK diagnostic logger levels are set to CRITICAL to avoid
routine request/authentication details. Do not enable verbose SDK logging with
real data or credentials. No application code logs prompts, responses, keys, or tokens.
The opt-in connection check sends only a fixed synthetic marker and never reads SQLite.
