"""Thread memory holds compact business context. Each run owns its transient work."""

import hmac
import re
import secrets
from collections import OrderedDict
from threading import Lock

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

from .catalogue import CITIES, DASHBOARDS, ROLES, TEAMS
from .conversation_plan import accepted_plan, choose_sources, context_for_question, discover, plan_analysis
from .demo_queries import Plan, analyse
from .field_queries import FieldPlan, analyse_fields, scope_tables
from .schema import DEMO_COLUMNS, DEMO_EXAMPLES

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
    discovery_mode: str
    selected: str
    plan: dict
    sources: list[str]
    known_sources: list[str]
    pending: str
    pending_digest: str
    stage: str
    notice: str
    runs: list[str]


class Turn(TypedDict):
    action: str
    question: str
    prior_question: str
    dashboard: str
    dashboards: list[dict]
    work: dict


def memory_vocabulary() -> set[str]:
    phrases = [
        "a an the this that those these it there they them me my we our you show tell give find get "
        "can could would I like know total overall actual actuals through across people number count "
        "what which where when why how many much was were is are do does did be been has have had "
        "in on at as from until since for of by into over between and or with to without than only "
        "all any each per total number average during last next week month year day daily weekly "
        "monday tuesday wednesday thursday friday compare comparison compared against instead use "
        "other different should both please about same versus vs break down split list "
        "filtered filter for count analyse analyze those people two starting measure breakdown dates office offices",
        *CITIES, *ROLES, *TEAMS, *DEMO_EXAMPLES,
    ]
    for report in DASHBOARDS.values():
        phrases.extend((report.title, report.description, *report.keywords))
        for measure in report.measures.values():
            phrases.extend((measure.label, measure.definition, *measure.aliases))
    for columns in DEMO_COLUMNS.values():
        for col, info in columns.items():
            phrases.extend((col, info["description"], *info["aliases"]))
    return set(re.findall(r"[a-z]+", " ".join(phrases).lower()))


MEMORY_WORDS = memory_vocabulary()


def memory_question(question: str) -> str:
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


def question_summary(question, matches):
    exact = memory_question(question)
    if exact:
        return exact
    topics = [DASHBOARDS[key].title for key in matches]
    topics.extend(city for city in CITIES if re.search(rf"\b{city}\b", question, re.I))
    return " · ".join(topics) or "Question about the demo data"


def original_wording(memory, supplied):
    if not memory.get("needs_original"):
        return memory["original_question"]
    if supplied and hmac.compare_digest(fingerprint(supplied), memory["original_digest"]):
        return supplied
    raise ValueError("Please enter the original question again to continue this discovery.")


def execute_plan(plan):
    if "fields" in plan:
        return analyse_fields(plan["base"], plan["joins"], FieldPlan.model_validate(plan["fields"]))
    return analyse(plan["dashboard"], Plan.model_validate(plan["metric_plan"]))


def prepare_turn(memory: Memory, runtime: Runtime[Turn]) -> dict:
    turn = runtime.context
    action, work = turn["action"], turn["work"]
    if action == "restore":
        if memory.get("stage") == "answer":
            work["evidence"] = execute_plan(memory["plan"])
        return {}
    if action == "discover":
        if memory.get("stage"):
            raise ValueError("Start a new conversation for a new discovery.")
        return {}
    if not memory.get("stage"):
        raise ValueError("This conversation has expired. Start a new question.")
    if action == "select":
        selected = memory.get("selected", "")
        if memory.get("stage") != "discover" or selected != turn["dashboard"] or selected not in memory["matches"]:
            raise ValueError("Select a recommended dashboard during discovery.")
        return {}
    question = turn["question"]
    if action == "answer_here":
        question = original_wording(memory, question)
    selected = memory.get("selected", "")
    if not selected and memory["matches"]:
        selected = memory["matches"][0]
    work["question"] = question
    context = context_for_question(question, selected, memory)
    prior = turn["prior_question"]
    if action == "followup" and prior and hmac.compare_digest(fingerprint(prior), memory.get("pending_digest", "")):
        context["pending_question"] = prior
    work["context"] = context
    history = memory["history"]
    if action == "followup":
        history = [*history, question_summary(question, [selected] if selected else [])][-HISTORY_SIZE:]
    return {"selected": selected, "history": history}


def discover_dashboards(memory: Memory, runtime: Runtime[Turn]) -> dict:
    turn = runtime.context
    question = turn["question"]
    matches, mode = discover(question, turn["dashboards"])
    remembered = memory_question(question)
    return dict(original_question=remembered, original_digest=fingerprint(question), needs_original=not remembered,
                history=[question_summary(question, matches)], selected="", matches=matches, discovery_mode=mode, stage="discover")


def clarification(work, message):
    work["clarification"] = message
    question = work["question"]
    wording = memory_question(question)
    return dict(stage="clarify", pending=wording or question_summary(question, []),
                pending_digest="" if wording else fingerprint(question),
                notice=memory_question(message) or "Please clarify the measure, city or period.")


def select_sources(memory: Memory, runtime: Runtime[Turn]) -> dict:
    work = runtime.context["work"]
    scope = choose_sources(work["context"])
    if scope.clarification or not scope.base:
        return clarification(work, scope.clarification or "What would you like to measure?")
    try:
        scope_tables(scope.base, scope.joins)
    except ValueError as error:
        return clarification(work, str(error))
    work["scope"] = scope
    return {}


def plan_query(memory: Memory, runtime: Runtime[Turn]) -> dict:
    work = runtime.context["work"]
    scope = work["scope"]
    decision = plan_analysis(work["context"], scope.base, scope.joins)
    try:
        work["plan"] = accepted_plan(decision, scope.base, scope.joins)
    except ValueError as error:
        return clarification(work, str(error))
    return {}


def query_sources(memory: Memory, runtime: Runtime[Turn]) -> dict:
    work = runtime.context["work"]
    plan = work["plan"]
    try:
        evidence = execute_plan(plan)
    except ValueError as error:
        return clarification(work, str(error))
    work["evidence"] = evidence
    summary = {"question": question_summary(work["question"], []), "plan": plan}
    known_sources = list(dict.fromkeys([*memory.get("known_sources", []), *evidence["sources"]]))
    return dict(stage="answer", plan=plan, sources=evidence["sources"], known_sources=known_sources,
                pending="", pending_digest="", notice="",
                analyses=[*memory.get("analyses", []), summary][-HISTORY_SIZE:])


def next_action(memory: Memory, runtime: Runtime[Turn]) -> str:
    action = runtime.context["action"]
    if action == "discover":
        return "discover_dashboards"
    if action in ("answer_here", "followup"):
        return "select_sources"
    return END


def after_scope(memory: Memory, runtime: Runtime[Turn]) -> str:
    return "plan_query" if "scope" in runtime.context["work"] else END


def after_plan(memory: Memory, runtime: Runtime[Turn]) -> str:
    return "query_sources" if "plan" in runtime.context["work"] else END


saver = InMemorySaver()
builder = StateGraph(Memory, context_schema=Turn)
for node in (prepare_turn, discover_dashboards, select_sources, plan_query, query_sources):
    builder.add_node(node)
builder.add_edge(START, "prepare_turn")
builder.add_conditional_edges("prepare_turn", next_action)
builder.add_edge("discover_dashboards", END)
builder.add_conditional_edges("select_sources", after_scope)
builder.add_conditional_edges("plan_query", after_plan)
builder.add_edge("query_sources", END)
conversation_graph = builder.compile(checkpointer=saver)
threads = OrderedDict()
turn_lock = Lock()


def replace_checkpoint(config, snapshot):
    saver.delete_thread(config["configurable"]["thread_id"])
    if snapshot:
        checkpoint = snapshot.checkpoint
        saver.put(config, checkpoint, snapshot.metadata, checkpoint["channel_versions"])


def public_state(memory, turn):
    dashboards = {item["dashboard_id"]: item for item in turn["dashboards"]}
    matches = [{field: dashboards[key][field] for field in
                ("dashboard_id", "title", "description", "dashboard_url", "url_status")} for key in memory.get("matches", [])]
    stage = memory.get("stage", "new")
    plan = memory.get("plan", {})
    return dict(stage=stage, original_question=memory.get("original_question", ""),
                needs_original=memory.get("needs_original", False), history=memory.get("history", []),
                dashboards=matches, discovery_mode=memory.get("discovery_mode", ""), selected=memory.get("selected", ""),
                filters=plan.get("metric_plan") or plan.get("fields", {}), sources=memory.get("sources", []),
                needs_prior_question=stage == "clarify" and bool(memory.get("pending_digest")),
                notice=turn["work"].get("clarification") or memory.get("notice", ""),
                evidence=turn["work"].get("evidence") if stage == "answer" else None)
