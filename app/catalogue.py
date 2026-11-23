"""The approved measures and join paths in the five fictional databases."""

import json
import os
import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

from .schema import DEMO_COLUMNS, DEMO_KEYS

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo_data"
CITIES = {"London": "LON", "Bristol": "BRS", "Manchester": "MAN"}
TEAMS = ("Platform", "Delivery", "Insights")
ROLES = ("Engineer", "Analyst", "Planner")


class Join(NamedTuple):
    target: str
    keys: tuple[tuple[str, str], ...]
    cardinality: str = "many-to-one"


# In this direction each fact row matches exactly one dimension row.
JOINS = {
    "employee_office": Join("locations.offices", (("employees.home_office_id", "offices.office_id"),)),
    "schedule_employee": Join("workforce.employees", (("weekly_schedule.employee_id", "employees.employee_id"),)),
    "visit_employee": Join("workforce.employees", (("badge_visits.employee_id", "employees.employee_id"),)),
    "visit_office": Join("locations.offices", (("badge_visits.visited_office_id", "offices.office_id"),)),
    "project_office": Join("locations.offices", (("portfolio_projects.lead_office_id", "offices.office_id"),)),
    "hiring_project": Join("portfolio.portfolio_projects", (("hiring_requests.project_id", "portfolio_projects.project_id"),)),
    "hiring_office": Join("locations.offices", (("hiring_requests.office_id", "offices.office_id"),)),
    "metrics_office": Join("locations.offices", (("office_daily_metrics.office_id", "offices.office_id"),)),
    "office_weather": Join("external.weather_daily", (("offices.city_code", "weather_daily.city_code"),
                                                       ("office_daily_metrics.metric_date", "weather_daily.date"))),
    "holiday_office": Join("locations.offices", (("holiday_calendar.office_id", "offices.office_id"),)),
}


class Measure(NamedTuple):
    label: str
    unit: str
    definition: str
    aggregate: str
    aliases: tuple[str, ...] = ()


class Dashboard(NamedTuple):
    title: str
    description: str
    keywords: tuple[str, ...]
    table: str
    joins: tuple[str, ...]
    measures: dict[str, Measure]
    date_column: str = ""
    condition: str = ""


DASHBOARDS = {
    "workforce": Dashboard("Workforce footprint", "Active employees and contracted capacity by home office.",
        ("employees", "workforce", "staff", "headcount", "fte"), "workforce.employees", ("employee_office",), {
            "employees": Measure("Active employees", "people", "Count distinct active workers by home office.", "COUNT(DISTINCT employees.employee_id)"),
            "workforce_fte": Measure("Active workforce capacity", "FTE", "Sum contracted FTE for active workers.", "SUM(employees.fte)"),
        }, condition="employees.active = 1"),
    "schedule": Dashboard("Weekly working patterns", "Planned onsite days from the agreed weekly schedule.",
        ("schedule", "scheduled", "planned onsite", "hybrid"), "workforce.weekly_schedule", ("schedule_employee", "employee_office"), {
            "planned_days": Measure("Planned onsite days", "person-days", "Sum the five weekday flags per worker-week.", "SUM(mon + tue + wed + thu + fri)"),
        }, "weekly_schedule.week_start"),
    "visits": Dashboard("Badge activity", "Building entries by the office actually visited.",
        ("badge", "visits", "entries"), "workforce.badge_visits", ("visit_office",), {
            "visits": Measure("Badge visits", "visits", "Count individual visits at the visited office.", "COUNT(badge_visits.visit_id)"),
        }, "badge_visits.visit_date"),
    "estate": Dashboard("Office estate", "Available desks and contracted monthly rent by office.",
        ("seats", "seat capacity", "desks", "rent"), "locations.offices", (), {
            "seats": Measure("Seat capacity", "seats", "Sum seats once per office.", "SUM(offices.seat_capacity)"),
            "floor_area": Measure("Floor area", "m²", "Sum internal floor area once per office.", "SUM(offices.floor_area_sqm)",
                                  ("floor space", "square metres", "square meters")),
            "rent": Measure("Monthly rent", "GBP/month", "Sum monthly leases once per office.", "SUM(offices.monthly_rent_gbp)"),
        }),
    "portfolio": Dashboard("Portfolio staffing", "Delivery capacity and unfilled demand on projects.",
        ("projects", "portfolio", "staffing gap", "shortfall", "assigned"), "portfolio.portfolio_projects", ("project_office",), {
            "staffing_gap": Measure("Project staffing gap", "FTE", "Sum positive required minus assigned FTE per project.", "SUM(MAX(0, required_fte - assigned_fte))"),
            "assigned": Measure("Assigned capacity", "FTE", "Sum assigned FTE per project.", "SUM(assigned_fte)"),
        }, "portfolio_projects.due_date"),
    "hiring": Dashboard("Hiring pipeline", "Open requisition capacity by hiring office and project.",
        ("hiring", "vacancies", "recruitment", "open fte"), "portfolio.hiring_requests", ("hiring_office",), {
            "open_fte": Measure("Open hiring capacity", "FTE", "Sum open FTE across open requisitions.", "SUM(hiring_requests.open_fte)"),
        }, "hiring_requests.expected_start_date", "hiring_requests.status = 'Open'"),
    "occupancy": Dashboard("Office occupancy", "Daily attendance and peak utilisation relative to seats.",
        ("occupancy", "utilisation", "utilization", "onsite headcount", "attendance"), "facilities.office_daily_metrics", ("metrics_office",), {
            "attendance": Measure("Recorded attendance", "person-days", "Sum daily distinct onsite counts. A person can attend on several days.", "SUM(office_daily_metrics.onsite_headcount)"),
            "utilisation": Measure("Peak seat utilisation", "%", "Capacity-weighted mean daily peak occupancy across recorded office-days.",
                                   "ROUND(100.0 * SUM(peak_occupancy) / NULLIF(SUM(seat_capacity), 0), 2)"),
        }, "office_daily_metrics.metric_date"),
    "cost": Dashboard("Workplace costs", "Metered energy and operating costs per recorded office-day.",
        ("energy", "electricity", "cost", "costs", "kwh"), "facilities.office_daily_metrics", ("metrics_office",), {
            "energy": Measure("Energy use", "kWh", "Sum metered energy per office-day.", "ROUND(SUM(energy_kwh), 2)",
                              ("power consumption", "meter readings")),
            "energy_cost": Measure("Energy cost", "GBP", "Sum electricity charges per office-day.", "ROUND(SUM(energy_cost_gbp), 2)"),
            "operating_cost": Measure("Operating cost", "GBP", "Sum energy and other daily costs, excluding rent.", "ROUND(SUM(energy_cost_gbp + other_cost_gbp), 2)"),
        }, "office_daily_metrics.metric_date"),
    "weather": Dashboard("Rain and attendance", "Attendance on days with recorded rain in the office city.",
        ("rain", "rainy", "weather", "rainfall"), "facilities.office_daily_metrics", ("metrics_office", "office_weather"), {
            "rain_attendance": Measure("Rainy-day attendance", "person-days", "Sum attendance only where the same city and date recorded precipitation.", "SUM(onsite_headcount)"),
        }, "office_daily_metrics.metric_date", "weather_daily.rain_mm > 0"),
    "closures": Dashboard("Office closures", "Closure days in the local holiday calendar.",
        ("closures", "closed", "holidays", "holiday"), "external.holiday_calendar", ("holiday_office",), {
            "closures": Measure("Office closures", "office-days", "Count office-local holiday dates marked closed.", "COUNT(holiday_calendar.holiday_date)"),
        }, "holiday_calendar.holiday_date", "holiday_calendar.office_closed = 1"),
}

def source_tables(report: Dashboard, joins=None) -> list[str]:
    paths = report.joins if joins is None else joins
    return [report.table] + [JOINS[name].target for name in paths]


def dashboard_sources(report_id: str) -> list[str]:
    report = DASHBOARDS[report_id]
    joins = list(report.joins)
    if report_id == "visits":
        joins.append("visit_employee")
    if report_id == "hiring":
        joins.append("hiring_project")
    return source_tables(report, joins)


def database_uri(directory: Path, name: str) -> str:
    path = directory / (name + ".db")
    if not path.is_file():
        raise FileNotFoundError(f"Missing demo asset: {path.name}")
    return path.resolve().as_uri() + "?mode=ro"


def read_dashboards(directory: Path = DEMO_DIR) -> list[dict]:
    uri = database_uri(directory, "locations")
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only = ON")
        records = db.execute("SELECT * FROM dashboard_catalog ORDER BY dashboard_id").fetchall()
    dashboards = []
    for record in records:
        item = dict(record)
        for key in ("keywords", "source_tables", "kpi_tags"):
            item[key] = json.loads(item.pop(key + "_json"))
        dashboards.append(item)
    config_path = os.getenv("NEXORA_DASHBOARD_URLS_FILE")
    if config_path:
        urls = json.loads(Path(config_path).read_text(encoding="utf-8"))
        if not isinstance(urls, dict) or set(urls) - DASHBOARDS.keys():
            raise ValueError("Dashboard URL configuration contains an unknown dashboard.")
        for item in dashboards:
            if item["dashboard_id"] in urls:
                url = urls[item["dashboard_id"]]
                parts = urlsplit(url) if isinstance(url, str) else None
                if not parts or parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
                    raise ValueError("Configured dashboard links must be HTTPS URLs without credentials.")
                item.update(dashboard_url=url, url_status="configured")
    return dashboards


def find_dashboards(question: str, dashboards: list[dict]) -> list[str]:
    words = set(re.findall(r"[a-z0-9]+", question.lower()))
    stop = set("a an the in what which is was were of and by on per office offices how many much are "
               "day days with without to from at for my it this that compared compare city cities about".split())
    words -= stop
    ranked = []
    for item in dashboards:
        terms = item["keywords"] + item["kpi_tags"] + [item["title"]]
        score = 0
        for term in terms:
            tokens = set(re.findall(r"[a-z0-9]+", term.lower())) - stop
            if tokens and tokens <= words:
                score += 3 * len(tokens)
        description = set(re.findall(r"[a-z0-9]+", item["description"].lower()))
        score += len(words & description)
        if score:
            ranked.append((-score, item["dashboard_id"]))
    ranked.sort()
    if not ranked:
        return []
    minimum = max(1, -ranked[0][0] / 3)
    return [report_id for score, report_id in ranked[:3] if -score >= minimum]


def find_measure_scope(question: str) -> str:
    """Look up business measures after Answer Here when discovery has no match."""
    wording = " " + " ".join(re.findall(r"[a-z0-9]+", question.lower())) + " "
    matches = set()
    for key, report in DASHBOARDS.items():
        for name, measure in report.measures.items():
            terms = (name.replace("_", " "), measure.label, *measure.aliases)
            if any(" " + term.lower() + " " in wording for term in terms):
                matches.add(key)
    return matches.pop() if len(matches) == 1 else ""


@lru_cache(maxsize=40)
def table_definition(table: str, directory: Path = DEMO_DIR) -> dict:
    columns = DEMO_COLUMNS[table]
    database, name = table.split(".")
    with closing(sqlite3.connect(database_uri(directory, database), uri=True)) as db:
        db.execute("PRAGMA query_only = ON")
        info = db.execute(f'PRAGMA table_info("{name}")').fetchall()
    keys = tuple(row[1] for row in sorted(info, key=lambda r: r[5]) if row[5])
    types = [(row[1], row[2]) for row in info]
    if types != [(field, data["type"]) for field, data in columns.items()] or keys != DEMO_KEYS[table]:
        raise sqlite3.DatabaseError(f"Catalogue mismatch for {table}.")
    return {"primary_key": keys, "columns": columns}


def schema_context(tables: list[str], joins) -> dict:
    return {
        "tables": {table: table_definition(table, DEMO_DIR) for table in tables},
        "joins": {name: JOINS[name]._asdict() for name in joins},
    }
