"""Opt-in live check; no database or employee data is accessed."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from .llm_connection import LLMConfigurationError, LLMConnectionError, create_completion, llm_connection


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marker: str


def main() -> int:
    print("LIVE connection check: sending synthetic data only (no database access).")
    prompt_file = Path(__file__).resolve().parent.parent / "prompts" / "connection_check.yml"
    instructions = yaml.safe_load(prompt_file.read_text(encoding="utf-8"))["template"]
    try:
        with llm_connection() as (client, model):
            response = create_completion(
                client, model, instructions, {"marker": "SYNTHETIC_CONNECTION_CHECK"},
                json_schema=CheckResult.model_json_schema(),
            )
        if not response.choices:
            raise ValueError
        choice = response.choices[0]
        if choice.finish_reason != "stop" or choice.message.refusal:
            raise ValueError
        result = CheckResult.model_validate_json(choice.message.content)
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
