"""Choose a dashboard from metadata, then plan an analysis when the user asks."""

from typing import Literal

from .catalogue import JOINS, find_dashboards, schema_context
from .demo_queries import METRIC_GROUPS, Plan, dimensions, needed_joins
from .field_queries import FieldPlan, Record
from .llm_connection import LLMConfigurationError, LLMConnectionError, LLMRequestRejectedError, create_completion, llm_connection
from .prompts import load_prompt
from .schema import CITIES, DEMO_COLUMNS
from .model_output import parse_output


class DashboardMatch(Record):
    dashboard_id: str
    coverage: Literal["exact", "related"]
    kpi: str
    dimensions: list[str]
    requested_offices: list[str]
    gap: Literal["none", "measure", "breakdown", "period", "scope", "calculation", "cause", "forecast"]
    reason: str


class Matches(Record):
    matches: list[DashboardMatch]
    clarification: str | None


GAPS = {
    "measure": "The requested measure is outside this view.",
    "breakdown": "The requested breakdown is outside this view.",
    "period": "Coverage of the requested period is unconfirmed.",
    "scope": "Coverage of the requested population or filter is unconfirmed.",
    "calculation": "The requested comparison or calculation needs a separate analysis.",
    "cause": "Explaining the cause requires investigation.",
    "forecast": "The requested forecast requires further analysis.",
}


class Scope(Record):
    base: str | None
    office_filters: list[str]
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
    except LLMRequestRejectedError:
        raise
    except (LLMConfigurationError, LLMConnectionError):
        raise LLMConnectionError("The language service is unavailable.") from None
    return parse_output(response, result_type)


def discover(question: str, dashboards: list[dict], context: dict) -> tuple[list[dict], str, str]:
    keys = ("dashboard_id", "title", "description", "keywords", "kpi_tags", "dimensions",
            "business_purpose", "supported_questions", "source_tables")
    compact = [{key: item[key] for key in keys} for item in dashboards]
    catalogue = {item["dashboard_id"]: item for item in compact}
    request = {"question": question, "catalogue": compact, "conversation_context": context, "office_locations": list(CITIES)}
    try:
        ranking = ask_model("dashboard_discovery", request, Matches)
    except (ValueError, LLMRequestRejectedError):
        return [], "invalid_ranking", ""
    except LLMConnectionError:
        matches = []
        for key in find_dashboards(question, dashboards):
            matches.append(dict(dashboard_id=key, coverage="related", reason=catalogue[key]["description"]))
        return matches, "keyword_fallback", ""

    if ranking.clarification is not None:
        clarification = " ".join(ranking.clarification.split())
        if ranking.matches or not 1 <= len(clarification) <= 200:
            return [], "invalid_ranking", ""
        return [], "semantic", clarification

    matches = []
    seen = set()
    for match in ranking.matches:
        key = match.dashboard_id
        card = catalogue.get(key)
        if not card or key in seen or match.kpi not in card["kpi_tags"]:
            continue
        if (match.coverage == "exact") != (match.gap == "none"):
            continue
        if not set(match.dimensions) <= set(card["dimensions"]):
            continue
        if match.coverage == "exact" and not set(match.requested_offices) <= set(CITIES):
            continue
        reason = " ".join(match.reason.split())
        if not 1 <= len(reason) <= 240:
            continue
        seen.add(key)
        basis = f"Shows {match.kpi.lower()} by {', '.join(card['dimensions'])}."
        matches.append(dict(dashboard_id=key, coverage=match.coverage, reason=reason,
                            basis=basis, limitation=GAPS.get(match.gap, ""),
                            outside_coverage=any(city not in CITIES for city in match.requested_offices)))
        if len(matches) == 3:
            break
    if ranking.matches and not matches:
        return [], "invalid_ranking", ""
    return matches, "semantic", ""


def choose_sources(context):
    paths = {name: {"requires": sorted({left.split(".")[0] for left, _ in join.keys}),
                    "adds": join.target, "cardinality": join.cardinality} for name, join in JOINS.items()}
    return ask_model("conversation_scope", {**context, "office_locations": list(CITIES), "table_topics": TOPICS, "approved_paths": paths}, Scope)


def plan_analysis(context, base, joins, tables):
    known = context["known_sources"]
    additional = [table for table in tables if table not in known]
    scoped = schema_context(additional, joins)
    # Stateless model calls still need column names for previously validated sources.
    # Only newly needed physical tables require schema inspection on a follow-up.
    prior = {table: {col: info["type"] for col, info in DEMO_COLUMNS[table].items()}
             for table in tables if table in known}
    metrics = {}
    for report_id, report in METRIC_GROUPS.items():
        if report.table != base:
            continue
        for key, measure in report.measures.items():
            metrics[key] = dict(dashboard=report_id, definition=measure.definition, unit=measure.unit,
                                breakdowns=list(dimensions(report_id)))
    payload = {**context, "base": base, "joins": joins, "relevant_schema": scoped,
               "previously_loaded_columns": prior, "defined_metrics": metrics, "city_codes": CITIES}
    return ask_model("conversation_plan", payload, Decision)


def validate_office_filters(decision, required_offices):
    if not required_offices:
        return
    if decision.plan:
        cities = set(decision.plan.cities)
    else:
        cities = set()
        names_by_code = {code: name for name, code in CITIES.items()}
        for rule in decision.fields.filters:
            field = rule.column.split(".")[-1]
            if field not in ("office_name", "city_code", "contract_city", "office_id",
                             "home_office_id", "visited_office_id", "lead_office_id"):
                continue
            if rule.operator in ("=", "in"):
                cities.update(names_by_code.get(value, value) for value in rule.values)
    if cities != set(required_offices):
        raise ValueError("I could not verify the office filter for this question. Which offices should I include?")


def accepted_plan(decision, base, joins, required_offices):
    if sum(item is not None for item in (decision.plan, decision.fields, decision.clarification)) != 1:
        raise ValueError("Which measure or fields would you like to explore?")
    if decision.clarification is not None:
        raise ValueError(decision.clarification.strip() or "Which measure or fields would you like to explore?")
    validate_office_filters(decision, required_offices)
    if decision.fields:
        return {"base": base, "joins": joins, "fields": decision.fields.model_dump()}
    for key, report in METRIC_GROUPS.items():
        if report.table == base and decision.plan and decision.plan.metric in report.measures:
            if set(needed_joins(key, decision.plan)) <= set(joins):
                return {"dashboard": key, "metric_plan": decision.plan.model_dump()}
    raise ValueError("This measure needs a different source or an additional approved join.")
