import os
from pathlib import Path

from .llm_connection import load_local_env

ROOT = Path(__file__).resolve().parent.parent


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required in the environment or .env.")
    return value


def positive_int(name: str) -> int:
    value = int(required_env(name))
    if value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


load_local_env()

MAX_QUESTION_LENGTH = positive_int("MAX_QUESTION_LENGTH")
MAX_RESULT_ROWS = positive_int("MAX_RESULT_ROWS")

DB_PATH = Path(required_env("DATABASE_PATH"))
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH

STATIC_DIR = ROOT / "static"
PROMPTS_DIR = ROOT / "prompts"
