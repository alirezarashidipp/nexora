"""Compile a grounded custom analysis using identifiers from its approved scope."""

import math
import re
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .catalogue import CITIES, DEMO_DIR, JOINS
from .demo_queries import join_clause, read_evidence
from .schema import DEMO_COLUMNS


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Projection(Record):
    column: str
    operation: Literal["field", "count", "count_distinct", "sum", "avg", "min", "max"]


class Filter(Record):
    column: str
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "in"]
    values: list[str | int | float]


class Sort(Record):
    position: int
    direction: Literal["asc", "desc"]


class FieldPlan(Record):
    projections: list[Projection]
    filters: list[Filter]
    distinct: bool
    sort: list[Sort]
    limit: int | None


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


def filter_value(column: str, info: dict, value):
    if info["type"] != "TEXT":
        if not isinstance(value, (int, float)) or abs(value) > 2**53 or not math.isfinite(value):
            raise ValueError("Use a finite number for the numeric filter.")
        if column.endswith(".employee_id") and (not isinstance(value, int) or not 1 <= value <= 12):
            raise ValueError("Choose a fictional worker ID from 1 to 12.")
        return value
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Choose a known categorical value or an ISO date.")
    representation = info["possible_values"]
    try:
        if representation in ("ISO YYYY-MM-DD", "ISO Monday"):
            if date.fromisoformat(value).isoformat() == value:
                return value
        elif representation == "HH:MM":
            if time.fromisoformat(value).isoformat(timespec="minutes") == value:
                return value
        elif representation == "ISO 8601 UTC":
            if value.endswith("Z") and datetime.fromisoformat(value).isoformat().replace("+00:00", "Z") == value:
                return value
        elif column.endswith(".project_id"):
            if re.fullmatch(r"P0[1-6]", value):
                return value
        elif column.endswith(".request_id"):
            if re.fullmatch(r"H0[1-6]", value):
                return value
        elif value in representation.split(", "):
            return value
    except ValueError:
        pass
    raise ValueError("Which recorded value should I use for " + column + "?")


def field_answer(rows, labels, titles, units):
    if not rows:
        return "No matching records for these filters."
    if len(rows) == 1 and all(value is None for value in rows[0].values()):
        return "No recorded value for these filters."
    if len(rows) > 5:
        return f"{len(rows)} matching rows."
    city_names = {code: city for city, code in CITIES.items()}
    lines = []
    for row in rows:
        parts = []
        for label, title, unit in zip(labels, titles, units):
            value = row[label]
            if isinstance(value, float):
                value = f"{value:,.2f}".rstrip("0").rstrip(".")
            elif value is None:
                value = "—"
            else:
                value = city_names.get(value, value)
            parts.append(f"{title}: {value} {unit}".strip())
        lines.append(" · ".join(parts))
    return "\n".join(lines)


def analyse_fields(base: str, joins: list[str], plan: FieldPlan, directory=DEMO_DIR) -> dict:
    tables = scope_tables(base, joins)
    columns = {table.split(".")[1] + "." + name: info
               for table in tables for name, info in DEMO_COLUMNS[table].items()}
    if not 1 <= len(plan.projections) <= 12 or len(plan.filters) > 12 or len(plan.sort) > len(plan.projections):
        raise ValueError("Choose a smaller set of fields or filters for this analysis.")
    selections, labels, titles, units, groups = [], [], [], [], []
    operations = {"field": "", "count": "count of", "count_distinct": "distinct", "sum": "total",
                  "avg": "average", "min": "minimum", "max": "maximum"}
    for item in plan.projections:
        col, op = item.column, item.operation
        if col not in columns:
            raise ValueError("Which field from the selected sources should I use?")
        field = col.split(".")[1]
        label = field if op == "field" else op + "_" + field
        if label in labels:
            label = col.replace(".", "_") if op == "field" else op + "_" + col.replace(".", "_")
        if label in labels:
            raise ValueError("Choose distinct output fields.")
        labels.append(label)
        name = columns[col]["aliases"][0]
        titles.append((operations[op] + " " + name).strip().capitalize())
        unit = columns[col]["unit"]
        if op in ("count", "count_distinct"):
            unit = "records" if op == "count" else "distinct values"
        units.append(unit)
        if op == "field":
            expr = col
            groups.append(col)
        elif op == "count_distinct":
            expr = f"COUNT(DISTINCT {col})"
        else:
            if op in ("sum", "avg") and (columns[col]["type"] == "TEXT" or not col.startswith(base.split(".")[1] + ".")):
                raise ValueError("Choose a numeric measure at the base table's grain.")
            expr = f"{op.upper()}({col})"
        selections.append(f'{expr} AS "{label}"')
    prefix = "SELECT DISTINCT " if plan.distinct else "SELECT "
    clauses = [prefix + ", ".join(selections), "FROM " + base]
    clauses.extend(join_clause(name) for name in joins)
    predicates, parameters = [], []
    for condition in plan.filters:
        col, op = condition.column, condition.operator
        if col not in columns or not 1 <= len(condition.values) <= 20:
            raise ValueError("Choose an available filter field and its values.")
        values = [filter_value(col, columns[col], value) for value in condition.values]
        if op == "in":
            predicates.append(f"{col} IN (" + ", ".join("?" for _ in values) + ")")
        elif len(values) == 1:
            predicates.append(f"{col} {op} ?")
        else:
            raise ValueError("Supply one value for this comparison.")
        parameters.extend(values)
    if predicates:
        clauses.append("WHERE " + " AND ".join(predicates))
    if groups and len(groups) < len(selections):
        clauses.append("GROUP BY " + ", ".join(groups))
    order = []
    for sort in plan.sort:
        if not 1 <= sort.position <= len(selections):
            raise ValueError("Choose one of the selected fields to sort by.")
        order.append(f"{sort.position} {sort.direction.upper()}")
    if order:
        clauses.append("ORDER BY " + ", ".join(order))
    if plan.limit is not None:
        if not 1 <= plan.limit < 2**63:
            raise ValueError("Choose a positive number of rows for the requested ranking.")
        clauses.append("LIMIT ?")
        parameters.append(plan.limit)
    sql = "\n".join(clauses)
    rows = read_evidence(sql, parameters, tables, directory)
    return dict(answer=field_answer(rows, labels, titles, units), definition=", ".join(titles),
                unit=units[0] if len(units) == 1 else "",
                sql=sql, parameters=parameters, rows=rows, sources=tables)
