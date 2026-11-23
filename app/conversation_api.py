import asyncio
from contextlib import aclosing
from typing import Literal
from uuid import UUID

from ag_ui.core import CustomEvent, EventType, RunAgentInput, RunErrorEvent
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from . import conversation
from .catalogue import read_dashboards
from .schema import DEMO_EXAMPLES

router = APIRouter()


class TurnAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["discover", "select", "answer_here", "followup", "restore"]
    dashboard: str = ""
    prior_question: str = ""
    original_token: str = ""
    clarification: str = ""


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


def public_run_error(message):
    question_errors = ("This turn has been superseded", "This conversation has expired", "Select a recommended dashboard",
                       "Please enter the original question", "Choose Answer Here")
    if message.startswith(question_errors):
        return RunErrorEvent(code="question", message=message)
    if message == "The language service is unavailable.":
        return RunErrorEvent(code="provider", message="The language service is unavailable. Retry when it is connected.")
    if message.startswith("Missing demo asset: "):
        dataset = message.removeprefix("Missing demo asset: ").removesuffix(".db")
        return RunErrorEvent(code="dataset", message=f"The {dataset} dataset is unavailable. Retry after it is restored.")
    if message.startswith(("Missing demo table: ", "no such table: ", "Catalogue mismatch for ")):
        return RunErrorEvent(code="dataset", message="The selected dataset does not match the catalogue. Retry after it is restored.")
    return RunErrorEvent(code="analysis", message="The analysis could not be completed. Please retry.")


def can_replay(runs, run_id, action):
    if runs[run_id] != action:
        return False
    run_ids = list(runs)
    later = [runs[key] for key in run_ids[run_ids.index(run_id) + 1:]]
    if action == "discover":
        return "discover" not in later
    if action == "select":
        return "discover" not in later and "select" not in later
    return not later


async def stream_turn(thread_id, run_id, intent, question, dashboard, dashboards):
    # The single-process demo serialises turns, including retries and checkpoint eviction.
    # A waiting request yields the event loop while the active SSE stream is running.
    while not conversation.turn_lock.acquire(blocking=False):
        await asyncio.sleep(0.025)
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    previous = None
    committed = False
    try:
        action = intent.action
        previous = conversation.saver.get_tuple(config)
        memory = previous.checkpoint["channel_values"] if previous else {}
        runs = dict(memory.get("runs", {}))
        replay_discovery = False
        if run_id in runs:
            replay_discovery = action in ("discover", "select")
            action = "restore" if can_replay(runs, run_id, action) else "superseded"
        elif action != "restore":
            runs[run_id] = action
            if len(runs) > 2 * conversation.HISTORY_SIZE:
                del runs[next(iter(runs))]
        turn = dict(thread_id=thread_id, action=action, question=question, prior_question=intent.prior_question,
                    original_token=intent.original_token, clarification=intent.clarification.strip(),
                    dashboard=dashboard, dashboards=dashboards, work={"replay_discovery": replay_discovery})
        # Only bounded trusted state enters the checkpointer. The user message, provider context
        # and SQL results travel in the LangGraph run context for the lifetime of this request.
        incoming = RunAgentInput(thread_id=thread_id, run_id=run_id, state={"runs": runs},
                                 messages=[], context=[], tools=[], forwarded_props={})
        async with aclosing(ConversationAgent(turn, memory).run(incoming)) as events:
            async for event in events:
                if event.type == EventType.RUN_ERROR:
                    yield public_run_error(event.message)
                    return
                if event.type == EventType.RUN_FINISHED:
                    latest = conversation.saver.get_tuple(config)
                    keep = previous if action == "restore" else latest
                    conversation.replace_checkpoint(config, keep)
                    if previous or action != "restore":
                        conversation.threads[thread_id] = None
                        conversation.threads.move_to_end(thread_id)
                    if len(conversation.threads) > conversation.MAX_THREADS:
                        oldest, _ = conversation.threads.popitem(last=False)
                        conversation.saver.delete_thread(oldest)
                    committed = True
                    if intent.action == "discover" and latest:
                        values = latest.checkpoint["channel_values"]
                        if values.get("needs_original") and values["original_digest"] == conversation.fingerprint(question):
                            token = conversation.seal_question(thread_id, question)
                            yield CustomEvent(name="original_question_token", value=token)
                event.raw_event = None
                yield event
    finally:
        try:
            if not committed:
                conversation.replace_checkpoint(config, previous)
        finally:
            conversation.turn_lock.release()


@router.get("/conversation", include_in_schema=False)
def conversation_page():
    return RedirectResponse("/employees")


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
    except (OSError, ValueError):
        raise HTTPException(503, "The dashboard catalogue is unavailable.") from None

    async def events():
        encoder = EventEncoder()
        stream = stream_turn(thread_id, run_id, intent, question, dashboard, dashboards)
        async with aclosing(stream) as events_for_turn:
            async for event in events_for_turn:
                yield encoder.encode(event)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
