# LLM connection

`app/llm_connection.py` contains plain functions using the official OpenAI Python
SDK and azure-identity. It has no application prompts or output validation.
The flow is explicit: load settings, create the selected client, use it, close
resources. Separate functions build requests and make SDK calls in the same file.

```python
from app.llm_connection import create_completion, llm_connection

with llm_connection() as (client, model):
    response = create_completion(
        client, model, instructions, task_data, json_schema=output_schema
    )
    # Validate the raw SDK response in application code.
    # Further calls here reuse the same client.
```

The root `.env` is loaded with `override=False` before connection settings are
read. Process environment variables win, including explicitly blank variables.
`LLM_PROVIDER` defaults to `openai` only when missing. All other values except
`openai` and `azure` are rejected. `LLM_TEMPERATURE` is always required and must
be a finite number from 0 to 2, including either endpoint.

OpenAI requires `OPENAI_API_KEY`; `OPENAI_MODEL` defaults to `gpt-4.1-mini` when
omitted. Azure requires all seven `AZURE_*` settings in `.env.example`. Endpoint,
scope, API version, and deployment must come from the target environment; no
Azure defaults are supplied. The deployment name is passed as `model`.

Azure authentication uses `EnvironmentCredential` and
`get_bearer_token_provider(credential, scope)`. The callback is passed directly
as `azure_ad_token_provider`, allowing lazy token acquisition and refresh.
`AZURE_OPENAI_AD_TOKEN` must be unset (even an empty value is rejected), because
the SDK would otherwise give that static token precedence over the callback.
Both clients use `timeout=60` and `max_retries=1`. The context closes the client
and credential, including when setup or a request fails. Each application
workflow node currently opens its own context; there is no process-wide pool.

Requests use `client.chat.completions.create`, system instructions, a separate
user message, `store=False`, and the configured temperature. Dictionaries/lists
use `json.dumps(..., ensure_ascii=False)`. No explicit token limit is set.
When supplied, the caller's JSON Schema uses `type=json_schema` and `strict=True`.
The model/deployment must support these parameters. Unsupported parameters are
not dropped or retried with changed semantics. Workflow schemas, refusal/empty
response checks, and Pydantic validation remain in `app/workflow.py`.

The SDKs use standard environment proxy support (`HTTPS_PROXY`, `NO_PROXY`).
No key, secret, token, task data, or raw provider error is printed by the app.
Errors are converted to safe messages; API configuration errors are HTTP 500
and provider/authentication failures are HTTP 502. Routine SDK diagnostics are
suppressed; do not turn on verbose SDK logging with sensitive data.

## Verification

```powershell
python -m unittest discover -s tests -v  # Offline mocked providers + database tests
python main.py --check                 # Offline database self-check only
python -m app.connection_check         # Explicit LIVE synthetic provider request
```

The live command does not import the API or read SQLite. It sends a fixed,
visibly synthetic marker and checks the strict JSON response. It prints a safe
success/failure message and exits nonzero on failure. It may incur API charges.
Imports and client setup do not issue live model requests. Offline tests do not
establish tenant permissions, network connectivity, or deployment compatibility.

The existing DeepEval judge remains OpenAI-based and needs an OpenAI key even
when application calls use Azure. This is separate from the synthetic check.

SDK references: [OpenAI Python SDK](https://github.com/openai/openai-python),
[Microsoft EnvironmentCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.environmentcredential?view=azure-python).
