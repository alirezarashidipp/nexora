"""Build aggregate SELECTs from the catalogue and execute them on read-only assets."""

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .catalogue import (
    CITIES, DASHBOARDS, DEMO_DIR, JOINS, Dashboard, Measure,
    database_uri, dashboard_sources, source_tables, table_definition,
)
from .schema import DEMO_COLUMNS


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    group_by: Literal["total", "city", "date", "team", "role", "project"]
    cities: list[Literal["London", "Bristol", "Manchester"]]
    team: Literal["Platform", "Delivery", "Insights"] | None
    role: Literal["Engineer", "Analyst", "Planner"] | None
    start_date: str | None
    end_date: str | None


def new_plan(metric: str) -> Plan:
    return Plan(metric=metric, group_by="total", cities=[], team=None, role=None, start_date=None, end_date=None)


def dimensions(report_id: str) -> dict[str, str]:
    report = DASHBOARDS[report_id]
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
    joins = list(DASHBOARDS[report_id].joins) if city_needed or report_id == "weather" else []
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
        if action == sqlite3.SQLITE_FUNCTION and column in ("count", "sum", "round", "nullif", "max"):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    with open_sources(directory, tables) as db:
        db.set_authorizer(authorize)
        rows = [dict(row) for row in db.execute(sql, parameters)]
        if not reads:
            raise ValueError("The analysis must read an approved source.")
        return rows


def query_filters(report: Dashboard, plan: Plan, fields: dict) -> tuple[list, list]:
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
    report = DASHBOARDS[report_id]
    fields = dimensions(report_id)
    if plan.metric not in report.measures:
        raise ValueError("Which measure from this dashboard should I use?")
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
    return "\n".join(clauses), parameters, source_tables(report, joins)


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
    measure = DASHBOARDS[report_id].measures[plan.metric]
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
        check_paths = [(report.table, report.joins) for report in DASHBOARDS.values()]
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
        mappings = {r[0]: json.loads(r[1]) for r in db.execute("SELECT dashboard_id, source_tables_json FROM locations.dashboard_catalog")}
        if mappings != {key: dashboard_sources(key) for key in DASHBOARDS}:
            raise ValueError("Dashboard source mappings do not match the approved catalogue.")
