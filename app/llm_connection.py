"""Provider configuration, authentication, and shared SDK request behavior."""

import json
import logging
import math
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path

from azure.core.exceptions import AzureError
from azure.identity import EnvironmentCredential, get_bearer_token_provider
from dotenv import load_dotenv
from openai import AzureOpenAI, OpenAI, OpenAIError

ROOT = Path(__file__).resolve().parent.parent

# SDK diagnostics can include raw authentication errors or request payloads.
for logger_name in ("azure", "msal", "openai", "httpx", "httpcore"):
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)


class LLMConfigurationError(ValueError):
    """A missing or invalid connection setting, safe to display."""


class LLMConnectionError(RuntimeError):
    """A provider failure with no sensitive diagnostic details."""


def load_local_env() -> None:
    load_dotenv(ROOT / ".env", override=False)


def required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise LLMConfigurationError(f"{name} is required and must not be blank.")
    return value


def read_temperature() -> float:
    load_local_env()
    raw = required_setting("LLM_TEMPERATURE")
    try:
        temperature = float(raw)
    except ValueError:
        raise LLMConfigurationError("LLM_TEMPERATURE must be a number from 0 to 2.") from None
    if not math.isfinite(temperature) or not 0 <= temperature <= 2:
        raise LLMConfigurationError("LLM_TEMPERATURE must be a number from 0 to 2.")
    return temperature


@contextmanager
def llm_connection():
    """Yield one reusable (client, model) pair and always close its resources."""
    load_local_env()
    provider = os.getenv("LLM_PROVIDER", "openai").strip()
    if provider not in {"openai", "azure"}:
        raise LLMConfigurationError("LLM_PROVIDER must be openai or azure.")
    read_temperature()

    try:
        with ExitStack() as resources:
            if provider == "openai":
                api_key = required_setting("OPENAI_API_KEY")
                model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
                if not model:
                    raise LLMConfigurationError("OPENAI_MODEL must not be blank.")
                client = OpenAI(api_key=api_key, timeout=60, max_retries=1)
            else:
                # Validate everything before allocating resources or acquiring tokens.
                if "AZURE_OPENAI_AD_TOKEN" in os.environ:
                    raise LLMConfigurationError(
                        "Remove AZURE_OPENAI_AD_TOKEN; Azure requires the refreshing token provider."
                    )
                for name in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
                    required_setting(name)
                scope = required_setting("AZURE_OPENAI_TOKEN_SCOPE")
                endpoint = required_setting("AZURE_OPENAI_ENDPOINT")
                api_version = required_setting("AZURE_OPENAI_API_VERSION")
                model = required_setting("AZURE_OPENAI_DEPLOYMENT")
                credential = EnvironmentCredential()
                resources.callback(credential.close)
                token_provider = get_bearer_token_provider(credential, scope)
                client = AzureOpenAI(
                    azure_endpoint=endpoint,
                    api_version=api_version,
                    azure_ad_token_provider=token_provider,
                    timeout=60,
                    max_retries=1,
                )
            resources.callback(client.close)
            yield client, model
    except AzureError:
        raise LLMConnectionError("Azure authentication or token acquisition failed. Check the Azure configuration and access.") from None
    except OpenAIError:
        raise LLMConnectionError("The LLM request failed. Check credentials, connectivity, and model parameter support.") from None


def create_completion(client, model: str, instructions: str, task_data, *, json_schema=None):
    """Return the raw SDK response; schemas and validation belong to the caller."""
    temperature = read_temperature()
    if isinstance(task_data, (dict, list)):
        task_data = json.dumps(task_data, ensure_ascii=False)
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": task_data},
        ],
        "temperature": temperature,
        "store": False,
    }
    if json_schema is not None:
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "result", "schema": json_schema, "strict": True},
        }
    try:
        return client.chat.completions.create(**request)
    except AzureError:
        raise LLMConnectionError("Azure authentication or token acquisition failed. Check the Azure configuration and access.") from None
    except OpenAIError:
        raise LLMConnectionError("The LLM request failed. Check credentials, connectivity, and model parameter support.") from None
