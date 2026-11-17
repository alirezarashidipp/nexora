import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAIError
from pydantic import BaseModel, Field

from .config import STATIC_DIR
from .llm_connection import LLMConfigurationError, LLMConnectionError
from .database import describe_table, table_schema
from .schema import EXAMPLE_QUESTIONS, TABLE_NAME
from .workflow import question_graph


class QuestionRequest(BaseModel):
    question: str = Field(min_length=2)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    describe_table()
    yield


app = FastAPI(title=f"{TABLE_NAME} SQL Assistant", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=FileResponse)
def index():
    return STATIC_DIR / "index.html"


@app.get("/schema")
def schema():
    return {
        "table": TABLE_NAME,
        "columns": table_schema(),
        "examples": EXAMPLE_QUESTIONS,
    }


@app.post("/ask")
def ask(payload: QuestionRequest):
    try:
        result = question_graph.invoke({"question": payload.question})
        return {
            "question": payload.question,
            "normalized_question": (
                result.get("normalized_question") or payload.question
            ),
            "status": result["status"],
            "answer": result["answer"],
            "sql": result.get("sql"),
            "rows": result.get("rows", []),
        }
    except LLMConfigurationError as error:
        raise HTTPException(500, str(error)) from None
    except LLMConnectionError:
        raise HTTPException(502, "The LLM service could not complete the request.") from None
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except (OpenAIError, sqlite3.Error) as error:
        raise HTTPException(502, "The upstream service or database request failed.") from None
