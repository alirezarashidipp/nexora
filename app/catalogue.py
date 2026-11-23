"""Load published dashboard metadata and resolve the approved analytical schema."""

import json
import os
import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .schema import DEMO_COLUMNS, DEMO_KEYS

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo_data"
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


def scope_tables(base: str, joins: list[str]) -> list[str]:
    if base not in DEMO_COLUMNS or base == "locations.dashboard_catalog":
        raise ValueError("Choose an available analytical source.")
    tables = [base]
    names = {base.split(".")[1]}
    for name in joins:
        join = JOINS.get(name)
        if not join or join.target in tables or any(left.split(".")[0] not in names for left, _ in join.keys):
            raise ValueError("These sources have no approved join at this grain.")
        tables.append(join.target)
        names.add(join.target.split(".")[1])
    return tables


def database_uri(directory: Path, name: str) -> str:
    path = directory / (name + ".db")
    if not path.is_file():
        raise FileNotFoundError(f"Missing demo asset: {path.name}")
    return path.resolve().as_uri() + "?mode=ro"


class PublishedDashboard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dashboard_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    keywords: list[str]
    kpi_tags: list[str] = Field(min_length=1)
    dimensions: list[str] = Field(min_length=1)
    business_purpose: str = Field(min_length=1)
    supported_questions: list[str] = Field(min_length=1)
    source_tables: list[str] = Field(min_length=1)
    dashboard_url: str | None


CATALOGUE_PATH = Path(__file__).with_name("dashboards.yml")


def published_dashboards() -> dict[str, dict]:
    try:
        entries = yaml.safe_load(CATALOGUE_PATH.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("Expected a list of dashboard descriptions.")
        catalogue = {}
        for entry in entries:
            card = PublishedDashboard.model_validate(entry)
            if card.dashboard_id in catalogue:
                raise ValueError("Duplicate dashboard ID.")
            if any(table not in DEMO_COLUMNS for table in card.source_tables):
                raise ValueError("Unknown source in the dashboard catalogue.")
            catalogue[card.dashboard_id] = card.model_dump()
        return catalogue
    except (yaml.YAMLError, ValidationError) as error:
        raise ValueError("Invalid dashboard catalogue.") from error


def configured_url(value) -> bool:
    if not isinstance(value, str) or any(ch.isspace() or ch == "\\" for ch in value):
        return False
    try:
        parts = urlsplit(value)
        hostname = (parts.hostname or "").lower().rstrip(".")
        reserved = ("example.com", "example.net", "example.org", "example", "invalid", "test")
        if any(hostname == domain or hostname.endswith("." + domain) for domain in reserved):
            return False
        return bool(parts.scheme == "https" and parts.hostname and parts.username is None
                    and parts.password is None and parts.port != 0)
    except ValueError:
        return False


def read_dashboards() -> list[dict]:
    """Read published metadata and optional destinations without opening any database."""
    dashboards = []
    config_path = os.getenv("NEXORA_DASHBOARD_URLS_FILE")
    urls = {}
    if config_path:
        try:
            urls = json.loads(Path(config_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            urls = None
    for key, item in published_dashboards().items():
        destination = item["dashboard_url"]
        item.update(dashboard_url=None, url_status="placeholder")
        if not isinstance(urls, dict):
            item["url_status"] = "unavailable"
        else:
            destination = urls.get(key, destination)
            if key not in urls and destination == f"https://example.org/#{key}":
                item.update(dashboard_url=destination, url_status="sample")
            elif configured_url(destination):
                item.update(dashboard_url=destination, url_status="configured")
            elif destination is not None and destination != "":
                item["url_status"] = "unavailable"
        dashboards.append(item)
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


@lru_cache(maxsize=40)
def table_definition(table: str, directory: Path = DEMO_DIR) -> dict:
    columns = DEMO_COLUMNS[table]
    database, name = table.split(".")
    with closing(sqlite3.connect(database_uri(directory, database), uri=True)) as db:
        db.execute("PRAGMA query_only = ON")
        info = db.execute(f'PRAGMA table_info("{name}")').fetchall()
    if not info:
        raise sqlite3.OperationalError(f"Missing demo table: {table}")
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
