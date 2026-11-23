"""Thread memory contains business context. Evidence lives only for the current run."""

import hmac
import re
import secrets
from collections import OrderedDict
from threading import Lock
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from .catalogue import CITIES, DASHBOARDS, ROLES, TEAMS, find_dashboards, find_measure_scope, read_dashboards
from .conversation_plan import example_plan, resolve_plan
from .demo_queries import Plan, analyse
from .schema import DEMO_EXAMPLES

MAX_THREADS = 64
HISTORY_SIZE = 6
TEXT_SIZE = 400
FINGERPRINT_KEY = secrets.token_bytes(32)


class Memory(TypedDict, total=False):
    original_question: str
    original_digest: str
    needs_original: bool
    history: list[str]
    analyses: list[dict]
    matches: list[str]
    selected: str
    plan: dict
    plan_dashboard: str
    sources: list[str]
    pending: str
    choosing_followup: bool
    choice_digest: str
    stage: str
    notice: str
    runs: list[str]


class Turn(TypedDict):
    action: str
    question: str
    dashboard: str
    dashboards: list[dict]
    evidence: dict


def memory_vocabulary() -> set[str]:
    phrases = [
        "a an the this that those these it there they them me my we our you show tell give find get "
        "can could would I like know total overall actual actuals through across people number count "
        "what which where when why how many much was were is are do does did be been has have had "
        "in on at as from until since for of by into over between and or with to without than only "
        "all any each per total number average during last next week month year day daily weekly "
        "monday tuesday wednesday thursday friday compare comparison compared against instead use "
        "other different should both please about same versus vs break down split list across during "
        "filtered filter for count analyse analyze those people two starting measure breakdown dates",
        *CITIES, *ROLES, *TEAMS, *DEMO_EXAMPLES,
    ]
    for report in DASHBOARDS.values():
        phrases.extend((report.title, report.description, *report.keywords))
        for measure in report.measures.values():
            phrases.extend((measure.label, measure.definition, *measure.aliases))
    return set(re.findall(r"[a-z]+", " ".join(phrases).lower()))


MEMORY_WORDS = memory_vocabulary()


def memory_question(question: str) -> str:
    """Keep only demo vocabulary and ISO dates. Never persist unknown free text."""
    if len(question) > TEXT_SIZE:
        return ""
    tokens = re.findall(r"\d{4}-\d{2}-\d{2}|[a-zA-Z]+|\s+|.", question)
    for token in tokens:
        if token.lower() in MEMORY_WORDS or token.isspace() or token in (".", "?", "!", ",", "-", "'"):
            continue
        if re.fullmatch(r"20\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])", token):
            continue
        return ""
    return question


def fingerprint(question: str) -> str:
    return hmac.digest(FINGERPRINT_KEY, question.encode("utf-8"), "sha256").hex()


def question_summary(question: str, report_ids: list[str]) -> str:
    exact = memory_question(question)
    if exact:
        return exact
    # Unknown words can contain personal details. Keep only known business context.
    topics = [DASHBOARDS[key].title for key in report_ids]
    topics.extend(city for city in CITIES if re.search(rf"\b{city}\b", question, re.I))
    return " · ".join(topics) or "Question about the demo data"


def original_wording(memory: Memory, supplied: str) -> str:
    if not memory.get("needs_original"):
        return memory["original_question"]
    if supplied and hmac.compare_digest(fingerprint(supplied), memory["original_digest"]):
        return supplied
    raise ValueError("Please enter the original question again to continue this discovery.")


def choose_scope(question: str, matches: list[str], previous="") -> str:
    if not matches:
        return find_measure_scope(question) or previous
    if len(matches) == 1:
        return matches[0]
    for key in matches:
        if example_plan(question, key):
            return key
    return previous if previous in matches else ""


def answer_question(question: str, selected: str, memory: Memory, turn: Turn) -> dict:
    decision = resolve_plan(question, selected, memory)
    notice = decision.clarification
    if decision.plan and not notice:
        try:
            evidence = analyse(selected, decision.plan)
        except ValueError as error:
            notice = str(error)
        else:
            turn["evidence"].update(evidence)
            accepted_plan = decision.plan.model_dump()
            summary = dict(question=question_summary(question, [selected]), dashboard=selected,
                           plan=accepted_plan, sources=evidence["sources"])
            analyses = [*memory.get("analyses", []), summary][-HISTORY_SIZE:]
            return dict(stage="answer", plan=accepted_plan, plan_dashboard=selected, sources=evidence["sources"],
                        analyses=analyses, pending="", notice="")
    # Only a business-safe clarification can enter the checkpoint.
    turn["evidence"]["clarification"] = notice
    saved_notice = memory_question(notice or "") or "Please clarify the measure, city or period."
    return dict(stage="clarify", pending=memory_question(question), notice=saved_notice)


def handle_turn(memory: Memory, runtime: Runtime[Turn]) -> dict:
    turn = runtime.context
    action = turn["action"]
    question = turn["question"]
    selected = memory.get("selected", "")
    if action == "discover":
        if memory.get("stage"):
            raise ValueError("Start a new conversation for a new discovery.")
        remembered = memory_question(question)
        matches = find_dashboards(question, turn["dashboards"])
        return dict(original_question=remembered, original_digest=fingerprint(question),
                    needs_original=not bool(remembered), history=[question_summary(question, matches)],
                    selected="", matches=matches, stage="discover")
    if not memory.get("stage"):
        raise ValueError("This conversation has expired. Start a new question.")
    if action == "select":
        selected = turn["dashboard"]
        choosing_followup = memory.get("choosing_followup", False)
        if selected not in memory["matches"] or (memory.get("plan") and not choosing_followup):
            raise ValueError("Select a recommended dashboard during discovery.")
        if not choosing_followup:
            return dict(selected=selected, stage="discover", notice="")
        if memory.get("pending"):
            question = memory["pending"]
        elif not question or not hmac.compare_digest(fingerprint(question), memory["choice_digest"]):
            return dict(selected=selected, stage="clarify", choosing_followup=False, choice_digest="",
                        notice="Please repeat your follow-up for the selected dashboard.")

    history = list(memory.get("history", []))
    matches = memory["matches"]
    if action == "answer_here":
        question = original_wording(memory, question)
        if not selected:
            selected = choose_scope(question, matches)
    elif action == "followup":
        found = find_dashboards(question, turn["dashboards"])
        selected = choose_scope(question, found, selected)
        if found:
            matches = found
        history.append(question_summary(question, [selected] if selected else matches))
    if not selected:
        notice = "Which dashboard should I use?" if matches else "Which business measure would you like to explore?"
        return dict(stage="clarify", notice=notice, selected="", matches=matches,
                    history=history[-HISTORY_SIZE:], pending=memory_question(question),
                    choosing_followup=action == "followup" and bool(matches), choice_digest=fingerprint(question))

    update = answer_question(question, selected, memory, turn)
    update.update(selected=selected, matches=matches, history=history[-HISTORY_SIZE:],
                  choosing_followup=False, choice_digest="")
    return update


saver = InMemorySaver()
builder = StateGraph(Memory, context_schema=Turn)
builder.add_node("handle_turn", handle_turn)
builder.add_edge(START, "handle_turn")
builder.add_edge("handle_turn", END)
conversation_graph = builder.compile(checkpointer=saver)
threads = OrderedDict()
turn_lock = Lock()


def replace_checkpoint(config, snapshot) -> None:
    # InMemorySaver in the pinned release keeps every checkpoint. Retain the
    # materialised final state using its public API, including all channel blobs.
    saver.delete_thread(config["configurable"]["thread_id"])
    if snapshot:
        checkpoint = snapshot.checkpoint
        saver.put(config, checkpoint, snapshot.metadata, checkpoint["channel_versions"])


def public_state(memory: dict, dashboards: list[dict], evidence: dict) -> dict:
    matches = []
    by_id = {item["dashboard_id"]: item for item in dashboards}
    for key in memory.get("matches", []):
        item = by_id[key]
        matches.append({field: item[field] for field in ("dashboard_id", "title", "description", "dashboard_url", "url_status")})
    stage = memory.get("stage", "new")
    if stage == "answer" and not evidence:
        evidence = analyse(memory["selected"], Plan.model_validate(memory["plan"]))
    return dict(stage=stage, original_question=memory.get("original_question", ""),
                needs_original=memory.get("needs_original", False), history=memory.get("history", []),
                dashboards=matches, selected=memory.get("selected", ""), filters=memory.get("plan", {}),
                choosing_followup=memory.get("choosing_followup", False),
                sources=memory.get("sources", []), notice=evidence.get("clarification") or memory.get("notice", ""),
                evidence=evidence if stage == "answer" else None)


def run_turn(thread_id: str, run_id: str, action: str, question="", dashboard="") -> dict:
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    dashboards = read_dashboards()
    with turn_lock:
        # A single process serialises the demo's turns, including eviction and retries.
        previous = saver.get_tuple(config) if thread_id in threads else None
        memory = previous.checkpoint["channel_values"] if previous else {}
        if previous:
            threads.move_to_end(thread_id)
        completed_runs = memory.get("runs", [])
        if action == "restore" or run_id in completed_runs:
            return public_state(memory, dashboards, {})
        if action != "discover" and not previous:
            raise ValueError("This conversation has expired. Start a new question.")
        evidence = {}
        turn = Turn(action=action, question=question, dashboard=dashboard,
                    dashboards=dashboards, evidence=evidence)
        try:
            runs = [*completed_runs, run_id][-2 * HISTORY_SIZE:]
            memory = conversation_graph.invoke({"runs": runs}, config, context=turn, durability="exit")
            latest = saver.get_tuple(config)
            replace_checkpoint(config, latest)
        except Exception:
            replace_checkpoint(config, previous)
            raise
        threads[thread_id] = None
        if len(threads) > MAX_THREADS:
            oldest, _ = threads.popitem(last=False)
            saver.delete_thread(oldest)
        return public_state(memory, dashboards, evidence)
