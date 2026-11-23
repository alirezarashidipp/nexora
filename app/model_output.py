"""Read complete SDK responses and validate structured model output."""

from typing import TypeVar

from pydantic import BaseModel, ValidationError

Output = TypeVar("Output", bound=BaseModel)


def response_text(response) -> str:
    choices = getattr(response, "choices", None)
    if not isinstance(choices, (list, tuple)) or not choices:
        raise ValueError("The model returned no choices.")
    choice = choices[0]
    message = getattr(choice, "message", None)
    if getattr(choice, "finish_reason", None) != "stop" or getattr(message, "refusal", None):
        raise ValueError("The model refused or did not complete the response.")
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("The model returned no content.")
    return content


def parse_output(response, output_type: type[Output]) -> Output:
    content = response_text(response)
    try:
        return output_type.model_validate_json(content)
    except ValidationError:
        raise ValueError("The model returned an invalid structured response.") from None
