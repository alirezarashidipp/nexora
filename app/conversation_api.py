import sqlite3
from typing import Literal
from uuid import UUID

from ag_ui.core import RunAgentInput, RunErrorEvent, RunFinishedEvent, RunStartedEvent, StateSnapshotEvent
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from .config import STATIC_DIR
from .conversation import run_turn
from .llm_connection import LLMConfigurationError, LLMConnectionError
from .schema import DEMO_EXAMPLES

router = APIRouter()


class TurnAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["discover", "select", "answer_here", "followup", "restore"]
    dashboard: str = ""


@router.get("/conversation", response_class=FileResponse)
def conversation_page():
    return STATIC_DIR / "conversation.html"


@router.get("/conversation/examples")
def example_questions():
    return list(DEMO_EXAMPLES)


@router.post("/ag-ui")
def agent_run(payload: RunAgentInput):
    try:
        thread_id = str(UUID(payload.thread_id))
        run_id = str(UUID(payload.run_id))
        intent = TurnAction.model_validate(payload.forwarded_props or {})
    except ValueError:
        raise HTTPException(422, "Use UUID thread/run IDs and a supported conversation action.") from None
    question = ""
    if payload.messages:
        last = payload.messages[-1]
        if last.role != "user" or not isinstance(last.content, str):
            raise HTTPException(422, "Send a plain-text user message for this turn.")
        question = last.content.strip()
    if intent.action in ("discover", "followup") and not question:
        raise HTTPException(422, "Enter a question.")

    def events():
        encoder = EventEncoder()
        yield encoder.encode(RunStartedEvent(thread_id=thread_id, run_id=run_id))
        try:
            state = run_turn(thread_id, run_id, intent.action, question, intent.dashboard)
        except (LLMConfigurationError, LLMConnectionError):
            yield encoder.encode(RunErrorEvent(code="provider", message="The language service is unavailable. Retry or use a suggested question."))
            return
        except (sqlite3.Error, OSError):
            yield encoder.encode(RunErrorEvent(code="source", message="The demo data or dashboard configuration is unavailable."))
            return
        except ValueError as error:
            yield encoder.encode(RunErrorEvent(code="question", message=str(error)))
            return
        yield encoder.encode(StateSnapshotEvent(snapshot=state))
        yield encoder.encode(RunFinishedEvent(thread_id=thread_id, run_id=run_id))

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
