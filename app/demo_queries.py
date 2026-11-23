"""Build aggregate SELECTs from the catalogue and execute them on read-only assets."""

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict

from .catalogue import (
    DEMO_DIR, JOINS, database_uri, published_dashboards, scope_tables, table_definition,
)
from .schema import CITIES, DEMO_COLUMNS, OFFICES


class Measure(NamedTuple):
    label: str
    unit: str
    definition: str
    aggregate: str
    aliases: tuple[str, ...] = ()


class MetricGroup(NamedTuple):
    table: str
    joins: tuple[str, ...]
    measures: dict[str, Measure]
    date_column: str = ""
    condition: str = ""


METRIC_GROUPS = {
    "workforce": MetricGroup("workforce.employees", ("employee_office",), {
            "employees": Measure("Active employees", "people", "Count distinct active workers by home office.", "COUNT(DISTINCT employees.employee_id)"),
            "workforce_fte": Measure("Active workforce capacity", "FTE", "Sum contracted FTE for active workers.", "SUM(employees.fte)"),
        }, condition="employees.active = 1"),
    "schedule": MetricGroup("workforce.weekly_schedule", ("schedule_employee", "employee_office"), {
            "planned_days": Measure("Planned onsite days", "person-days", "Sum the five weekday flags per worker-week.", "SUM(mon + tue + wed + thu + fri)"),
        }, "weekly_schedule.week_start"),
    "visits": MetricGroup("workforce.badge_visits", ("visit_office",), {
            "visits": Measure("Badge visits", "visits", "Count individual visits at the visited office.", "COUNT(badge_visits.visit_id)"),
        }, "badge_visits.visit_date"),
    "estate": MetricGroup("locations.offices", (), {
            "seats": Measure("Seat capacity", "seats", "Sum seats once per office.", "SUM(offices.seat_capacity)"),
            "floor_area": Measure("Floor area", "m²", "Sum internal floor area once per office.", "SUM(offices.floor_area_sqm)",
                                  ("floor space", "square metres", "square meters")),
            "rent": Measure("Monthly rent", "GBP/month", "Sum monthly leases once per office.", "SUM(offices.monthly_rent_gbp)"),
        }),
    "portfolio": MetricGroup("portfolio.portfolio_projects", ("project_office",), {
            "staffing_gap": Measure("Project staffing gap", "FTE", "Sum positive required minus assigned FTE per project.", "SUM(MAX(0, required_fte - assigned_fte))"),
            "assigned": Measure("Assigned capacity", "FTE", "Sum assigned FTE per project.", "SUM(assigned_fte)"),
        }, "portfolio_projects.due_date"),
    "hiring": MetricGroup("portfolio.hiring_requests", ("hiring_office",), {
            "open_fte": Measure("Open hiring capacity", "FTE", "Sum open FTE across open requisitions.", "SUM(hiring_requests.open_fte)"),
        }, "hiring_requests.expected_start_date", "hiring_requests.status = 'Open'"),
    "occupancy": MetricGroup("facilities.office_daily_metrics", ("metrics_office",), {
            "attendance": Measure("Recorded attendance", "person-days", "Sum daily distinct onsite counts. A person can attend on several days.", "SUM(office_daily_metrics.onsite_headcount)"),
            "utilisation": Measure("Peak seat utilisation", "%", "Capacity-weighted mean daily peak occupancy across recorded office-days.",
                                   "ROUND(100.0 * SUM(peak_occupancy) / NULLIF(SUM(seat_capacity), 0), 2)"),
        }, "office_daily_metrics.metric_date"),
    "cost": MetricGroup("facilities.office_daily_metrics", ("metrics_office",), {
            "energy": Measure("Energy use", "kWh", "Sum metered energy per office-day.", "ROUND(SUM(energy_kwh), 2)",
                              ("power consumption", "meter readings")),
            "energy_cost": Measure("Energy cost", "GBP", "Sum electricity charges per office-day.", "ROUND(SUM(energy_cost_gbp), 2)"),
            "operating_cost": Measure("Operating cost", "GBP", "Sum energy and other daily costs, excluding rent.", "ROUND(SUM(energy_cost_gbp + other_cost_gbp), 2)"),
        }, "office_daily_metrics.metric_date"),
    "weather": MetricGroup("facilities.office_daily_metrics", ("metrics_office", "office_weather"), {
            "rain_attendance": Measure("Rainy-day attendance", "person-days", "Sum attendance only where the same city and date recorded precipitation.", "SUM(onsite_headcount)"),
        }, "office_daily_metrics.metric_date", "weather_daily.rain_mm > 0"),
    "closures": MetricGroup("external.holiday_calendar", ("holiday_office",), {
            "closures": Measure("Office closures", "office-days", "Count office-local holiday dates marked closed.", "COUNT(holiday_calendar.holiday_date)"),
        }, "holiday_calendar.holiday_date", "holiday_calendar.office_closed = 1"),
}


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    group_by: Literal["total", "city", "date", "team", "role", "project"]
    cities: list[str]
    team: Literal["Platform", "Delivery", "Insights"] | None
    role: Literal["Engineer", "Analyst", "Planner"] | None
    start_date: str | None
    end_date: str | None


def dimensions(report_id: str) -> dict[str, str]:
    report = METRIC_GROUPS[report_id]
    fields = {"city": "offices.city_code"}
    if report.date_column:
        fields["date"] = report.date_column
    if report_id in ("workforce", "schedule", "visits"):
        fields.update(team="employees.team_id", role="employees.role")
    if report_id == "portfolio":
        fields.update(team="portfolio_projects.team_id", project="portfolio_projects.project_name")
    if report_id == "hiring":
        fields.update(team="hiring_requests.team_id", role="hiring_requests.role", project="portfolio_projects.project_name")
    return fields


def needed_joins(report_id: str, plan: Plan) -> list[str]:
    city_needed = bool(plan.cities) or plan.group_by == "city" or plan.metric == "utilisation"
    joins = list(METRIC_GROUPS[report_id].joins) if city_needed or report_id == "weather" else []
    if report_id == "schedule" and not joins and (plan.team or plan.role or plan.group_by in ("team", "role")):
        joins.append("schedule_employee")
    if report_id == "visits" and (plan.team or plan.role or plan.group_by in ("team", "role")):
        joins.append("visit_employee")
    if report_id == "hiring" and plan.group_by == "project":
        joins.append("hiring_project")
    return joins


def join_clause(name: str, left=False) -> str:
    join = JOINS[name]
    pairs = [f"{first} = {second}" for first, second in join.keys]
    prefix = "LEFT JOIN" if left else "JOIN"
    return f"{prefix} {join.target} ON " + " AND ".join(pairs)


@contextmanager
def open_sources(directory: Path, tables):
    # The in-memory connection is only a namespace for read-only attached files.
    with closing(sqlite3.connect(":memory:", uri=True)) as db:
        db.row_factory = sqlite3.Row
        names = sorted({table.split(".")[0] for table in tables})
        for name in names:
            db.execute(f'ATTACH DATABASE ? AS "{name}"', (database_uri(directory, name),))
        db.execute("PRAGMA query_only = ON")
        yield db


def read_evidence(sql: str, parameters: list, tables: list[str], directory: Path) -> list[dict]:
    if not sql.startswith("SELECT ") or any(token in sql for token in (";", "--", "/*")):
        raise ValueError("A single read-only SELECT is required.")
    allowed = {table: table_definition(table, directory)["columns"] for table in tables}
    reads = set()

    def authorize(action, name, column, database, source):
        if action == sqlite3.SQLITE_READ:
            table = f"{database}.{name}"
            if table in allowed and (not column or column in allowed[table]):
                reads.add(table)
                return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and column in ("count", "sum", "round", "nullif", "max", "min", "avg"):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    with open_sources(directory, tables) as db:
        db.set_authorizer(authorize)
        rows = [dict(row) for row in db.execute(sql, parameters)]
        if not reads:
            raise ValueError("The analysis must read an approved source.")
        return rows


def query_filters(report: MetricGroup, plan: Plan, fields: dict) -> tuple[list, list]:
    if any(city not in CITIES for city in plan.cities):
        raise ValueError("That office is outside the available data. Which covered office should I use?")
    if len(plan.cities) != len(set(plan.cities)):
        raise ValueError("Choose distinct cities to compare.")
    predicates = [report.condition] if report.condition else []
    parameters = []
    if plan.cities:
        places = ", ".join("?" for city in plan.cities)
        predicates.append(f"offices.city_code IN ({places})")
        parameters.extend(CITIES[city] for city in plan.cities)
    for kind, value in (("team", plan.team), ("role", plan.role)):
        if value:
            if kind not in fields:
                raise ValueError(f"This measure has no {kind} filter. Which measure should I use?")
            predicates.append(f"{fields[kind]} = ?")
            parameters.append(value)
    for operator, value in ((">=", plan.start_date), ("<=", plan.end_date)):
        if value is not None:
            if not report.date_column:
                raise ValueError("This measure is a snapshot. Should I use it without a date filter?")
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                raise ValueError("Which calendar date should I use (YYYY-MM-DD)?") from None
            if parsed.isoformat() != value:
                raise ValueError("Use calendar dates in YYYY-MM-DD format.")
            predicates.append(f"{report.date_column} {operator} ?")
            parameters.append(value)
    if plan.start_date and plan.end_date and plan.start_date > plan.end_date:
        raise ValueError("Which start and end dates should I use, in chronological order?")
    return predicates, parameters


def build_query(report_id: str, plan: Plan) -> tuple[str, list, list[str]]:
    report = METRIC_GROUPS[report_id]
    fields = dimensions(report_id)
    if plan.metric not in report.measures:
        raise ValueError("Which measure from this source should I use?")
    if plan.group_by != "total" and plan.group_by not in fields:
        raise ValueError("Which available breakdown should I use for this measure?")
    measure = report.measures[plan.metric]
    joins = needed_joins(report_id, plan)
    predicates, parameters = query_filters(report, plan, fields)
    group = fields.get(plan.group_by)
    projection = f"{group} AS {plan.group_by}, " if group else ""
    clauses = [f"SELECT {projection}{measure.aggregate} AS value", f"FROM {report.table}"]
    clauses.extend(join_clause(name) for name in joins)
    if predicates:
        clauses.append("WHERE " + " AND ".join(predicates))
    if group:
        clauses.extend([f"GROUP BY {group}", f"ORDER BY {group}"])
    return "\n".join(clauses), parameters, scope_tables(report.table, joins)


def format_answer(measure: Measure, plan: Plan, rows: list[dict]) -> str:
    display_cities = {code: city for city, code in CITIES.items()}
    values = []
    for row in rows:
        if row["value"] is None:
            continue
        label = row.get(plan.group_by, "")
        if plan.group_by == "city":
            label = display_cities[label]
        amount = f'{row["value"]:,.2f}'.rstrip("0").rstrip(".")
        values.append(f"{label}: {amount} {measure.unit}" if label else f"{amount} {measure.unit}")
    answer = measure.label + " — " + ", ".join(values) + "." if values else "No matching records for these filters."
    if plan.group_by == "city" and values:
        seen = {row["city"] for row in rows}
        missing = [city for city in plan.cities if CITIES[city] not in seen]
        if missing:
            answer += " No matching records for " + ", ".join(missing) + "."
    return answer


def analyse(report_id: str, plan: Plan, directory: Path = DEMO_DIR) -> dict:
    sql, parameters, tables = build_query(report_id, plan)
    rows = read_evidence(sql, parameters, tables, directory)
    measure = METRIC_GROUPS[report_id].measures[plan.metric]
    answer = format_answer(measure, plan, rows)
    return dict(answer=answer, sql=sql, parameters=parameters, rows=rows,
                sources=tables, definition=measure.definition, unit=measure.unit)


def check_demo_data(directory: Path = DEMO_DIR) -> None:
    """Offline provisioning/self-check. Discovery never calls this function."""
    with open_sources(directory, DEMO_COLUMNS) as db:
        databases = {name.split(".")[0] for name in DEMO_COLUMNS}
        for database in databases:
            live = {r[0] for r in db.execute(f"SELECT name FROM {database}.sqlite_master WHERE type='table'")}
            expected = {name.split(".")[1] for name in DEMO_COLUMNS if name.startswith(database + ".")}
            if live != expected:
                raise ValueError(f"Unexpected tables in {database}.")
        for qualified in DEMO_COLUMNS:
            table_definition(qualified, directory)
            count = db.execute(f"SELECT COUNT(*) FROM {qualified}").fetchone()[0]
            if not 1 <= count <= 20:
                raise ValueError(f"Unexpected fixture row count in {qualified}.")
        check_paths = [(report.table, report.joins) for report in METRIC_GROUPS.values()]
        check_paths.extend([("workforce.badge_visits", ("visit_employee",)), ("portfolio.hiring_requests", ("hiring_project",))])
        for base, joins in check_paths:
            count = db.execute(f"SELECT COUNT(*) FROM {base}").fetchone()[0]
            source = base
            for name in joins:
                source += " " + join_clause(name, left=True)
                key = JOINS[name].keys[0][1]
                found = db.execute(f"SELECT COUNT(*), COUNT({key}) FROM {source}").fetchone()
                if tuple(found) != (count, count):
                    raise ValueError(f"Missing or non-unique relationship: {name}")
        offices = {r[0]: tuple(r[1:]) for r in db.execute(
            "SELECT office_name, office_id, country_code, timezone FROM locations.offices")}
        if offices != {city: details[:3] for city, details in OFFICES.items()}:
            raise ValueError("The office directory must contain the six agreed locations.")
        for table, field in (
            ("workforce.employees", "home_office_id"), ("workforce.badge_visits", "visited_office_id"),
            ("portfolio.portfolio_projects", "lead_office_id"), ("portfolio.hiring_requests", "office_id"),
            ("facilities.office_daily_metrics", "office_id"), ("external.weather_daily", "city_code"),
            ("external.holiday_calendar", "office_id"),
        ):
            sites = {r[0] for r in db.execute(f"SELECT DISTINCT {field} FROM {table}")}
            if sites != set(CITIES.values()):
                raise ValueError(f"Expected all six offices in {table}.")
        stored = db.execute("SELECT dashboard_id, source_tables_json FROM locations.dashboard_catalog")
        stored_sources = {row[0]: json.loads(row[1]) for row in stored}
        published_sources = {key: card["source_tables"] for key, card in published_dashboards().items()}
        if stored_sources != published_sources:
            raise ValueError("Dashboard source mappings do not match the published catalogue.")
