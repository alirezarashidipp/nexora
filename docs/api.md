# API

FastAPI serves the frontend and JSON endpoints. When running locally, Swagger UI
is available at <http://127.0.0.1:8000/docs>.

## `GET /`

Returns the conversation workspace. `GET /conversation` opens the same page.
`GET /employees` opens the original employee workspace.

## POST /ag-ui

Send an AG-UI `RunAgentInput` and accept `text/event-stream`. Use a UUID for
each new conversation and a fresh run UUID for each action. Retry a failed
delivery with the same run ID. The server uses the last plain-text user message
and its own checkpoint. Arbitrary client state never becomes conversation memory.

```json
{
  "threadId": "056d8656-a33e-46bb-a9e4-60a547dfacf9",
  "runId": "8ea2878a-f074-4efb-88a2-f4af9b0b497e",
  "messages": [{"id": "q1", "role": "user", "content": "How many active employees are in Bristol?"}],
  "forwardedProps": {"action": "discover"}
}
```

| Action | Input | Result |
| --- | --- | --- |
| `discover` | First question in a new thread | Ranked dashboard metadata |
| `select` | `forwardedProps.dashboard` from the recommendations | Synchronised selection |
| `answer_here` | Same thread, optional original message | Answer to the original question or a clarification |
| `followup` | New user message, same thread | Contextual analysis or a clarification |
| `restore` | Thread ID and empty messages | Current view, or a fresh view for an expired thread |

A successful stream emits `RUN_STARTED`, `STATE_SNAPSHOT`, then `RUN_FINISHED`.
An execution failure emits `RUN_ERROR` and preserves the previous successful turn.
A malformed request receives HTTP 422. SQL parameters are positional in the
order shown. `GET /conversation/examples` returns the four offline demo questions.

## `GET /schema`

Returns the fixed `data` table, its live SQLite columns combined with
`COLUMN_GUIDE` and example questions.

Example response:

```json
{
  "table": "data",
  "columns": [
    {
      "name": "EMPLOYEE_ID",
      "type": "INTEGER PRIMARY KEY",
      "description": "Unique numeric identifier for one employee.",
      "possible_values": "Integer IDs from 1001 to 1100 in the demo data."
    },
    {
      "name": "EMPLOYEE_NAME",
      "type": "TEXT NOT NULL",
      "description": "Synthetic full name of the employee.",
      "possible_values": "100 fictional demo names, such as AVA CARTER and LEO KOWALSKI."
    }
  ],
  "examples": ["How many coders?"]
}
```

## `POST /ask`

Request:

```json
{
  "question": "How many coders are in MSW?"
}
```

Successful response:

```json
{
  "question": "How many coders are in MSW?",
  "normalized_question": "How many CODER employees are in MSW?",
  "status": "valid",
  "answer": "There are 20 coders in MSW.",
  "sql": "SELECT COUNT(*) AS total FROM data WHERE STATUS = 'CODER' AND DEPARTMENT = 'MSW'",
  "rows": [{"total": 20}]
}
```

`status` is one of:

- `valid`: SQL was generated and executed.
- `incomplete`: the response asks one clarification; `sql` is `null`.
- `invalid`: the request is outside the fixed `data` table or requests a write;
  `sql` is `null`.

## Errors

| Status | Meaning |
| --- | --- |
| `400` | Workflow or SQL validation rejected the request |
| `422` | Request body or minimum question length failed API validation |
| `500` | Selected provider settings or temperature are missing/invalid |
| `502` | LLM provider/authentication or SQLite failed; raw provider details are hidden |

The endpoint is synchronous and each request is independent.

There is no application maximum question length or returned-row cap.
