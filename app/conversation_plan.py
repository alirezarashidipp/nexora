"""Choose a dashboard from metadata, then plan an analysis when the user asks."""

from .catalogue import CITIES, DASHBOARDS, JOINS, find_dashboards, schema_context
from .demo_queries import Plan, dimensions, needed_joins
from .field_queries import FieldPlan, Record, scope_tables
from .llm_connection import LLMConfigurationError, LLMConnectionError, create_completion, llm_connection
from .prompts import load_prompt
from .schema import DEMO_COLUMNS
from .workflow import parse_output


class Matches(Record):
    dashboard_ids: list[str]


class Scope(Record):
    base: str | None
    joins: list[str]
    clarification: str | None


class Decision(Record):
    plan: Plan | None
    fields: FieldPlan | None
    clarification: str | None


TOPICS = {
    "workforce.employees": "One worker per row. Current workforce, active status, home office, role and contracted capacity.",
    "workforce.weekly_schedule": "One worker-week. Agreed working pattern and planned onsite weekdays.",
    "workforce.badge_visits": "One building entry. Actual site visited, arrival and departure.",
    "locations.offices": "One office. Location, timezone, seats, floor area and monthly rent.",
    "portfolio.portfolio_projects": "One portfolio project. Priority, deadline, required and assigned staff.",
    "portfolio.hiring_requests": "One requisition. Role, status, open capacity and expected start.",
    "facilities.office_daily_metrics": "One office-day. Actual and peak attendance, energy, cost and data quality.",
    "external.weather_daily": "One city-day. Temperature, rain, local alerts and observations.",
    "external.holiday_calendar": "One office-date. Local public holidays and closures.",
}


def ask_model(prompt, payload, result_type):
    try:
        with llm_connection() as (client, model):
            response = create_completion(client, model, load_prompt(prompt), payload,
                                         json_schema=result_type.model_json_schema())
    except (LLMConfigurationError, LLMConnectionError):
        raise LLMConnectionError("The language service is unavailable.") from None
    return parse_output(response, result_type)


def discover(question: str, dashboards: list[dict]) -> tuple[list[str], str]:
    keys = ("dashboard_id", "title", "description", "keywords", "kpi_tags")
    compact = [{key: item[key] for key in keys} for item in dashboards]
    known = {item["dashboard_id"] for item in compact}
    try:
        result = ask_model("dashboard_discovery", {"question": question, "catalogue": compact}, Matches)
        ids = result.dashboard_ids
        if len(ids) > 3 or len(set(ids)) != len(ids) or not set(ids) <= known:
            raise ValueError("Invalid dashboard ranking.")
        return ids, "semantic"
    except (ValueError, LLMConnectionError):
        return find_dashboards(question, dashboards), "keyword_fallback"


def context_for_question(question, selected, memory):
    return {"question": question, "selected_dashboard": selected or None,
            "original_question": memory.get("original_question", ""),
            "pending_question": memory.get("pending", ""), "last_clarification": memory.get("notice", ""),
            "recent_questions": memory.get("history", []), "previous_plan": memory.get("plan", {}),
            "recent_analyses": memory.get("analyses", []), "known_sources": memory.get("known_sources", [])}


def choose_sources(context):
    paths = {name: {"requires": sorted({left.split(".")[0] for left, _ in join.keys}),
                    "adds": join.target, "cardinality": join.cardinality} for name, join in JOINS.items()}
    return ask_model("conversation_scope", {**context, "table_topics": TOPICS, "approved_paths": paths}, Scope)


def plan_analysis(context, base, joins):
    tables = scope_tables(base, joins)
    known = context["known_sources"]
    additional = [table for table in tables if table not in known]
    scoped = schema_context(additional, joins)
    # Stateless model calls still need column names for previously validated sources.
    # Only newly needed physical tables require schema inspection on a follow-up.
    prior = {table: {col: info["type"] for col, info in DEMO_COLUMNS[table].items()}
             for table in tables if table in known}
    metrics = {}
    for report_id, report in DASHBOARDS.items():
        if report.table != base:
            continue
        for key, measure in report.measures.items():
            metrics[key] = dict(dashboard=report_id, definition=measure.definition, unit=measure.unit,
                                breakdowns=list(dimensions(report_id)))
    payload = {**context, "base": base, "joins": joins, "relevant_schema": scoped,
               "previously_loaded_columns": prior, "defined_metrics": metrics, "city_codes": CITIES}
    return ask_model("conversation_plan", payload, Decision)


def accepted_plan(decision, base, joins):
    if sum(item is not None for item in (decision.plan, decision.fields, decision.clarification)) != 1:
        raise ValueError("Which measure or fields would you like to explore?")
    if decision.clarification:
        raise ValueError(decision.clarification)
    if decision.fields:
        return {"base": base, "joins": joins, "fields": decision.fields.model_dump()}
    for key, report in DASHBOARDS.items():
        if report.table == base and decision.plan and decision.plan.metric in report.measures:
            if set(needed_joins(key, decision.plan)) <= set(joins):
                return {"dashboard": key, "metric_plan": decision.plan.model_dump()}
    raise ValueError("This measure needs a different source or an additional approved join.")
