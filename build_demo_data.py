"""Build the five fictional SQLite assets offline, in a new directory."""

import argparse
import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from app.catalogue import CITIES, DASHBOARDS, DEMO_DIR, ROLES, TEAMS, dashboard_sources
from app.schema import DEMO_COLUMNS, DEMO_KEYS, OFFICES


def sample_rows() -> dict[str, list]:
    rows = {name: [] for name in DEMO_COLUMNS}
    offices = list(CITIES.items())
    week = date(2026, 11, 16)
    days = [(week + timedelta(days=i)).isoformat() for i in range(5)]
    for i, (city, office) in enumerate(offices):
        _, country, timezone, region = OFFICES[city]
        rows["locations.offices"].append((office, city, office, country, timezone, 8 + i, 96 + 12*i, 3200 + 350*i))
        rows["external.holiday_calendar"].append((office, "2026-11-23", "Demo office day", "Local", country, region, 1, "Demo calendar"))
    visits = rows["workforce.badge_visits"]
    team_capacity = {team: 0.0 for team in TEAMS}
    for i in range(12):
        city, office = offices[i % len(offices)]
        active = int(i != 10)
        team = TEAMS[(i // 2) % 3]
        worker_fte = 0.8 if i % 3 == 0 else 1.0
        employee = (i+1, team, ROLES[(i//3) % 3], office, city,
                    "Contractor" if i % 4 == 0 else "Employee", worker_fte, active)
        rows["workforce.employees"].append(employee)
        if not active:
            continue
        team_capacity[team] += worker_fte
        weekdays = [0] * 5
        if i != 11:
            weekdays[i % 3] = 1
            if i < 7:
                weekdays[(i+2) % 3] = 1
        rows["workforce.weekly_schedule"].append((i+1, days[0], *weekdays, "Hybrid" if any(weekdays) else "Remote"))
        for weekday, planned in enumerate(weekdays):
            if planned:
                site = "LON" if i == 1 and weekday == 1 else office
                visits.append((len(visits)+1, i+1, site, days[weekday], "09:00", "16:30",
                               "Home" if site == office else "Visiting", "Demo badge reader"))
    for i, (_, office) in enumerate(offices):
        for j, workday in enumerate(days[:3]):
            people = {v[1] for v in visits if v[2] == office and v[3] == workday}
            headcount = len(people)
            energy = 12 + i*2 + 3.5*headcount
            rows["facilities.office_daily_metrics"].append((office, workday, headcount, headcount, energy, round(energy*.25, 2), 8+i, "Verified"))
            rain = (0, 2, 0, 6, 1)[(i+j) % 5]
            rows["external.weather_daily"].append((office, workday, 9+i+j/2, rain, "Rain" if rain else "Clear",
                                                   "Yellow" if rain >= 6 else "None", "Demo weather", workday+"T18:00:00Z"))
    for i, name in enumerate(("Harbour", "Meridian", "Orchard", "Copper", "Beacon", "Willow")):
        pid = f"P{i+1:02}"
        team = TEAMS[i % 3]
        office = offices[i % len(offices)][1]
        assigned = round(team_capacity[team] / 2, 2)
        gap = (i % 3) * 0.5
        due = (week + timedelta(days=42 + 7*i)).isoformat()
        rows["portfolio.portfolio_projects"].append((pid, name, team, office, assigned+gap, assigned, due, ("High","Medium","Low")[i % 3]))
        rows["portfolio.hiring_requests"].append((f"H{i+1:02}", pid, team, office, ROLES[i % 3], gap,
                                                 "2026-12-01", "Open" if gap else "Filled"))
    for key, report in DASHBOARDS.items():
        tags = [measure.label for measure in report.measures.values()]
        rows["locations.dashboard_catalog"].append((key, report.title, report.description, json.dumps(tags),
            json.dumps(dashboard_sources(key)), f"https://example.org/dashboards/{key}", "placeholder", json.dumps(report.keywords)))
    return rows


def build_databases(output: Path) -> None:
    if output.exists():
        raise ValueError("Choose a new output directory to preserve the existing databases.")
    rows = sample_rows()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as staging:
        assets = Path(staging) / "assets"
        assets.mkdir()
        for qualified, columns in DEMO_COLUMNS.items():
            database, table = qualified.split(".")
            definitions = [f'{name} {info["type"]} NOT NULL' for name, info in columns.items()]
            definitions.append("PRIMARY KEY (" + ", ".join(DEMO_KEYS[qualified]) + ")")
            placeholders = ", ".join("?" for _ in columns)
            with closing(sqlite3.connect(assets / (database + ".db"))) as db:
                with db:
                    db.execute(f"CREATE TABLE {table} (" + ", ".join(definitions) + ")")
                    db.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows[qualified])
        from app.demo_queries import check_demo_data
        check_demo_data(assets)
        assets.rename(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEMO_DIR)
    build_databases(parser.parse_args().output)
