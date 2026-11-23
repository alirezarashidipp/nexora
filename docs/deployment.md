# Deployment

## Local development

```powershell
Copy-Item .env.example .env
# Add OPENAI_API_KEY to .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

When `python` is not on `PATH`, use the full path to `python.exe` to create
`.venv`; commands after activation use the virtual environment automatically.

`main.py` starts Uvicorn on `127.0.0.1:8000` with reload enabled. This entry
point is for local development only.

Local runs load project-root `.env` with python-dotenv; process variables win.
Older `.env.local` settings must be moved to `.env`. Configure either the OpenAI
key/model or the seven Azure settings listed in `.env.example`, plus the required
`LLM_TEMPERATURE`. Azure values must come from the target environment.
See [LLM connection](llm-connection.md). Docker Compose reads `.env` at runtime.

Both AG-UI Python packages are pinned in `requirements.txt`. Run the conversation
workspace with one worker for in-memory threads. Allow streaming SSE when deploying
behind a proxy. With the language service unavailable, keyword dashboard discovery
works and analytical turns return a provider error.

## Docker Compose

```powershell
docker compose up --build
```

Compose:

- binds the app to `127.0.0.1:8000`;
- reads secrets at runtime from `.env`;
- uses the prebuilt fictional `main_datawarehouse.db` included in the image;
- opens SQLite read-only and never creates or seeds it.

Stop the app with:

```powershell
docker compose down
```

## Standalone Docker

```powershell
docker build -t employee-sql-assistant .
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 employee-sql-assistant
```

The image already contains `/app/main_datawarehouse.db` for the demo.

For another externally produced database containing a table named `data`, mount
the file read-only and point `DATABASE_PATH` at its container location:

```powershell
$database = (Resolve-Path "C:\path\external.db").Path
docker run --rm --env-file .env -e DATABASE_PATH=/data/external.db --mount "type=bind,source=$database,target=/data/external.db,readonly" -p 127.0.0.1:8000:8000 employee-sql-assistant
```

## Production boundary

Run the conversation demo with one Uvicorn worker. Its LangGraph
`InMemorySaver` is process-local and is cleared by restart or development reload.
The 64 most recently used threads can be restored. The service reads the five
prebuilt database files in `demo_data/` and never provisions them on startup.
To override dashboard links in Docker, mount a JSON file read-only and set
`NEXORA_DASHBOARD_URLS_FILE` to its in-container path.

The image runs Uvicorn as a non-root user, but the application is not ready for
an untrusted public network. Add authentication, rate limiting, request
timeouts, safe operational logging, and TLS at the deployment edge first.

Back up the externally managed SQLite file before schema or data migrations.
The application has no migration system and never modifies that file.
