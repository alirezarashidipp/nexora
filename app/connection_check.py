"""Opt-in live check; no database or employee data is accessed."""

from pydantic import BaseModel, ConfigDict

from .llm_connection import LLMConfigurationError, LLMConnectionError, create_completion, llm_connection
from .model_output import parse_output
from .prompts import load_prompt


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marker: str


def main() -> int:
    print("LIVE connection check: sending synthetic data only (no database access).")
    instructions = load_prompt("connection_check")
    try:
        with llm_connection() as (client, model):
            response = create_completion(
                client, model, instructions, {"marker": "SYNTHETIC_CONNECTION_CHECK"},
                json_schema=CheckResult.model_json_schema(),
            )
        result = parse_output(response, CheckResult)
        if result.marker != "SYNTHETIC_CONNECTION_CHECK":
            raise ValueError
    except LLMConfigurationError as error:
        print(f"Configuration invalid; no successful live check: {error}")
        return 1
    except LLMConnectionError:
        print("Live check failed. Check credentials, network, and model parameter support.")
        return 1
    except (ValueError, TypeError):
        print("Live check failed: invalid configuration or synthetic response.")
        return 1
    print("Live provider request succeeded; synthetic structured response validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
