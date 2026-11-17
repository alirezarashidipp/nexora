import os
from pathlib import Path

from .llm_connection import load_local_env

ROOT = Path(__file__).resolve().parent.parent


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required in the environment or .env.")
    return value


load_local_env()

DB_PATH = Path(required_env("DATABASE_PATH"))
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH

STATIC_DIR = ROOT / "static"
PROMPTS_DIR = ROOT / "prompts"
