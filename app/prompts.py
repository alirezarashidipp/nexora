from functools import cache
from pathlib import Path

import yaml

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    document = yaml.safe_load(
        (PROMPTS_DIR / f"{name}.yml").read_text(encoding="utf-8")
    )
    template = document.get("template") if isinstance(document, dict) else None
    if not isinstance(template, str) or not template.strip():
        raise ValueError(f"Prompt {name!r} must contain a non-empty template.")
    return template.strip()


def render_prompt(name: str, **values: object) -> str:
    return load_prompt(name).format(**values)
