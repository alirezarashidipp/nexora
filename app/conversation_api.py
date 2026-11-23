import asyncio
import sqlite3
from contextlib import aclosing
from typing import Literal
from uuid import UUID

from ag_ui.core import EventType, RunAgentInput, RunErrorEvent
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from . import conversation
from .catalogue import read_dashboards
from .config import STATIC_DIR
from .schema import DEMO_EXAMPLES

router = APIRouter()


class TurnAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["discover", "select", "answer_here", "followup", "restore"]
    dashboard: str = ""
    prior_question: str = ""


class ConversationAgent(LangGraphAgent):
    def __init__(self, turn, memory):
        super().__init__(name="Nexora", graph=conversation.conversation_graph, emit_raw_events=False)
        self.turn = turn
        self.memory = dict(memory)

    def get_stream_kwargs(self, *args, **kwargs):
        options = super().get_stream_kwargs(*args, **kwargs)
        options.update(context=self.turn, durability="exit")
        return options

    def get_state_snapshot(self, state):
        self.memory.update(state)
        return conversation.public_state(self.memory, self.turn)


async def stream_turn(thread_id, run_id, action, question, dashboard, dashboards, prior_question):
    # The single-process demo serialises turns, including retries and checkpoint eviction.
    # A waiting request yields the event loop while the active SSE stream is running.
    while not conversation.turn_lock.acquire(blocking=False):
        await asyncio.sleep(0.025)
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    previous = None
    committed = False
    try:
        previous = conversation.saver.get_tuple(config)
        memory = previous.checkpoint["channel_values"] if previous else {}
        runs = memory.get("runs", [])
        if run_id in runs:
            action = "restore"
        elif action != "restore":
            runs = [*runs, run_id][-2 * conversation.HISTORY_SIZE:]
        turn = dict(action=action, question=question, prior_question=prior_question,
                    dashboard=dashboard, dashboards=dashboards, work={})
        # Only bounded trusted state enters the checkpointer. The user message, provider context
        # and SQL results travel in the LangGraph run context for the lifetime of this request.
        shared_state = {"runs": runs}
        if action == "select" and memory.get("stage") == "discover" and dashboard in memory.get("matches", []):
            shared_state["selected"] = dashboard
        incoming = RunAgentInput(thread_id=thread_id, run_id=run_id, state=shared_state,
                                 messages=[], context=[], tools=[], forwarded_props={})
        async with aclosing(ConversationAgent(turn, memory).run(incoming)) as events:
            async for event in events:
                if event.type == EventType.RUN_ERROR:
                    safe_messages = ("Start a new conversation", "This conversation has expired",
                                     "Select a recommended dashboard", "Please enter the original question")
                    if event.message.startswith(safe_messages):
                        yield RunErrorEvent(code="question", message=event.message)
                    elif event.message == "The language service is unavailable.":
                        yield RunErrorEvent(code="provider", message="The language service is unavailable. Retry when it is connected.")
                    else:
                        yield RunErrorEvent(code="analysis", message="The analysis could not be completed. Please retry.")
                    return
                if event.type == EventType.RUN_FINISHED:
                    if action == "restore":
                        conversation.replace_checkpoint(config, previous)
                    else:
                        conversation.replace_checkpoint(config, conversation.saver.get_tuple(config))
                    if previous or action != "restore":
                        conversation.threads[thread_id] = None
                        conversation.threads.move_to_end(thread_id)
                    if len(conversation.threads) > conversation.MAX_THREADS:
                        oldest, _ = conversation.threads.popitem(last=False)
                        conversation.saver.delete_thread(oldest)
                    committed = True
                event.raw_event = None
                yield event
    finally:
        try:
            if not committed:
                conversation.replace_checkpoint(config, previous)
        finally:
            conversation.turn_lock.release()


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
    dashboard = intent.dashboard
    if intent.action == "select" and isinstance(payload.state, dict):
        dashboard = payload.state.get("selected", dashboard)
        if not isinstance(dashboard, str):
            raise HTTPException(422, "Select a dashboard by its catalogue ID.")
    try:
        dashboards = read_dashboards()
    except (OSError, sqlite3.Error, ValueError):
        raise HTTPException(503, "The dashboard catalogue is unavailable.") from None

    async def events():
        encoder = EventEncoder()
        stream = stream_turn(thread_id, run_id, intent.action, question, dashboard, dashboards, intent.prior_question)
        async with aclosing(stream) as events_for_turn:
            async for event in events_for_turn:
                yield encoder.encode(event)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
