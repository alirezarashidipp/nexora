"""Resolve a business question into one of the catalogue's aggregate measures."""

import re

from pydantic import BaseModel, ConfigDict

from .catalogue import CITIES, DASHBOARDS, schema_context, source_tables
from .demo_queries import Plan, dimensions, new_plan
from .llm_connection import create_completion, llm_connection
from .prompts import load_prompt
from .schema import DEMO_EXAMPLES
from .workflow import parse_output


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: Plan | None
    clarification: str | None


def example_plan(question: str, report_id: str) -> Plan | None:
    words = question.casefold().strip(" .?!")
    for example, (report, metric) in DEMO_EXAMPLES.items():
        if report != report_id:
            continue
        pattern = example.casefold().rstrip("?")
        for city in CITIES:
            pattern = pattern.replace(city.lower(), "{city}")
        for city in CITIES:
            if words == pattern.format(city=city.lower()):
                plan = new_plan(metric)
                plan.cities = [city]
                return plan
    return None


def city_followup(question: str, previous: dict, pending: str) -> Decision | None:
    places = "|".join(CITIES)
    text = question.strip(" .?!")
    compare = re.fullmatch(rf"compare (?:it|that|this)(?: with| to) ({places})", text, re.I)
    replace = re.fullmatch(rf"(?:what about|how about|instead use) ({places})", text, re.I)
    baseline = re.fullmatch(places, text, re.I)
    outstanding = re.fullmatch(rf"compare (?:it|that|this)(?: with| to) ({places})[.?!]?", pending, re.I)
    if not compare and not replace and not (baseline and outstanding):
        return None
    if not previous:
        return Decision(plan=None, clarification="Which measure and starting city should I compare?")
    plan = Plan.model_validate(previous)
    if compare:
        city = compare[1].title()
        if plan.group_by not in ("total", "city"):
            return Decision(plan=None, clarification="Should I compare city totals instead of the previous breakdown?")
        if not plan.cities or plan.cities == [city]:
            return Decision(plan=None, clarification=f"Which other city should I compare with {city}?")
        plan.cities = list(dict.fromkeys([*plan.cities, city]))
        plan.group_by = "city"
    elif replace:
        plan.cities = [replace[1].title()]
    else:
        cities = [baseline[0].title(), outstanding[1].title()]
        if cities[0] == cities[1]:
            return Decision(plan=None, clarification="Which two different cities should I compare?")
        plan.cities = cities
        plan.group_by = "city"
    return Decision(plan=plan, clarification=None)


def resolve_plan(question: str, report_id: str, memory: dict) -> Decision:
    previous = memory.get("plan") or {}
    pending = memory.get("pending", "")
    direct = example_plan(question, report_id)
    if direct:
        return Decision(plan=direct, clarification=None)
    if report_id == memory.get("plan_dashboard") or not previous:
        comparison = city_followup(question, previous, pending)
        if comparison:
            return comparison
    report = DASHBOARDS[report_id]
    detail = (question + " " + pending).lower()
    location = any(city.lower() in detail for city in CITIES) or re.search(r"cit(?:y|ies)|office|utili[sz]ation", detail)
    joins = list(report.joins) if report_id == "weather" or location or previous.get("cities") else []
    if report_id == "schedule" and not joins and re.search(r"team|role|employee", detail):
        joins.append("schedule_employee")
    if report_id == "visits" and (re.search(r"team|role|engineer|analyst|planner|platform|delivery|insights", detail)
                                  or previous.get("role") or previous.get("team")):
        joins.append("visit_employee")
    if report_id == "hiring" and "project" in detail:
        joins.append("hiring_project")
    known = memory.get("sources", [])
    additional = [table for table in source_tables(report, joins) if table not in known]
    inputs = {
        "question": question, "original_question": memory.get("original_question", ""),
        "pending_question": pending, "recent_questions": memory.get("history", []),
        "previous_plan": previous, "previous_dashboard": memory.get("plan_dashboard"), "dashboard": report.title,
        "recent_analyses": memory.get("analyses", []),
        "measures": {key: {"definition": value.definition, "unit": value.unit, "aliases": value.aliases}
                     for key, value in report.measures.items()},
        "breakdowns": list(dimensions(report_id)), "known_sources": known,
        "relevant_schema": schema_context(additional, joins),
    }
    with llm_connection() as (client, model):
        response = create_completion(client, model, load_prompt("conversation_plan"), inputs,
                                     json_schema=Decision.model_json_schema())
    decision = parse_output(response, Decision)
    if decision.clarification or not decision.plan:
        return Decision(plan=None, clarification=decision.clarification or "Which measure and filters should I use?")
    return decision
