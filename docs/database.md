# Database

## Conversation fixtures

The five files in `demo_data/` contain only fictional data. The catalogue
in `app/schema.py` defines ordered columns, SQLite types, aliases, possible
values, units and primary keys. `app/dashboards.yml` owns the published dashboard-to-table mappings.
`app/demo_queries.py` defines business measures and their aggregation grain. Both metric
and custom queries use the approved join paths in `app/catalogue.py`.

| Database | Tables | Grain |
| --- | --- | --- |
| workforce.db | employees, weekly_schedule, badge_visits | Worker, worker-week, visit |
| locations.db | offices, dashboard_catalog | Office, dashboard |
| portfolio.db | portfolio_projects, hiring_requests | Project, requisition |
| facilities.db | office_daily_metrics | Office and local date |
| external.db | weather_daily, holiday_calendar | City and date, office and holiday date |

London (LON), Manchester (MAN), Dubai (DXB), Mumbai (BOM), Bengaluru (BLR) and
Shanghai (SHA) each have one fictional office. Office records carry their country
and IANA timezone. Holiday records use the local country and region.

The weekly schedule starts on 16 November 2026. Facilities and weather contain the
same three observed days, 16–18 November, in all six offices (18 rows per table).
Badge entries determine daily attendance. Rainy-day attendance joins both city and
local date. Attendance summed across days counts person-days. Workforce headcount
counts distinct active employees at their home office.

Rebuild into a new directory with `python build_demo_data.py --output demo_rebuilt`.
The builder uses fixed dates and inputs, validates all ten tables and join
cardinalities and publishes the directory only after validation. Stop the service
before replacing fixture files. `python main.py --check` validates the shipped set.
The 20-row ceiling applies to fixture provisioning. Queries return every matching group.

Discovery reads the published dashboard descriptions from `app/dashboards.yml`. Edit the list to change the number of published dashboards. The offline fixture builder also reads this file for its dashboard descriptions. Each qualified source identifies both the database and the table. The shipped fixture contains six metadata rows and empty destinations.
The workspace displays a link only when its destination has been configured. An absent or
unusable destination leaves the recommendation and Answer Here available.
Set `dashboard_url` in a YAML entry to its verified HTTPS destination. Deployments may override individual destinations through the existing `NEXORA_DASHBOARD_URLS_FILE` JSON mapping. An empty override disables a link. Reserved example domains are rejected. Link validation checks the URL format, not authentication or reachability.

The published IDs are `workforce`, `estate`, `portfolio`, `hiring`, `occupancy` and `cost`.
The independent analytical catalogue still supports schedules, badge visits, weather and closures through Answer Here.

## Existing-file contract

`DATABASE_PATH=main_datawarehouse.db` resolves relative to the project root. The tracked
file already contains 100 fictional employees. The application never creates,
migrates, or seeds a database. The exposed table name is fixed as `data` in
`app/schema.py`; it is not an environment setting.

At startup, the application:

1. requires `DATABASE_PATH` to point to an existing file;
2. opens SQLite with URI `mode=ro`;
3. verifies that the `data` table exists;
4. requires `COLUMN_GUIDE` keys to exactly match the live columns.

Default schema:

| Column | Type | Meaning |
| --- | --- | --- |
| `EMPLOYEE_ID` | `INTEGER PRIMARY KEY` | Unique employee ID |
| `EMPLOYEE_NAME` | `TEXT NOT NULL` | Synthetic employee name |
| `DEPARTMENT` | `TEXT NOT NULL` | `MSW`, `MRM`, or `SECURITY` |
| `STATUS` | `TEXT NOT NULL` | `CODER` or `NON-CODER` |
| `TITLE` | `TEXT NOT NULL` | Official job title |

## Supply another SQLite file

For a flat CSV, the standalone `csv_to_sqlite.py` tool can act as the external
data producer. It uses only Python's standard library and is never imported by
the application:

```powershell
.\.venv\Scripts\python.exe csv_to_sqlite.py `
  .\input.csv `
  .\main_datawarehouse.db `
  --infer-types `
  --replace
```

The tool creates one flat table. It does not infer primary keys, indexes,
relationships, or `NOT NULL` constraints. It refuses an existing output by
default; `--replace` atomically replaces the complete database after a
successful conversion. Stop the app before replacing its configured file.
`main.py --check` validates the database selected in `.env`; deterministic
unit tests intentionally continue to use the sample database from `.env.example`
unless the project's default dataset is deliberately changed.

1. Create and populate the file in the external data process.
2. Stop the application.
3. Ensure its table is named `data` and set `DATABASE_PATH` in `.env`.
4. Update `COLUMN_GUIDE` to match `data`'s exact columns.
5. Update `EXAMPLE_QUESTIONS` and `EVAL_CASES` when the dataset changes.
6. Restart the application.

No change is required in `app/database.py`, `app/workflow.py`, the prompt
YAML, or the frontend.

## Change data or columns

The external producer owns all `CREATE TABLE`, `ALTER TABLE`, `INSERT`,
`UPDATE`, and migration work. After replacing or migrating the SQLite file:

1. update `COLUMN_GUIDE` and the schema-specific `EVAL_CASES` when columns or
   their meaning changed;
2. keep every guide key identical to its live SQLite column name;
3. restart the application to clear cached schema metadata;
4. run the deterministic tests and `python main.py --check` from the active
   virtual environment;
5. run the opt-in DeepEval suite when an OpenAI key is available.

Never commit real employee or personal data.

## Swap the database engine

The modular boundary supports replacing SQLite files that expose `data`, not
replacing SQLite itself. PostgreSQL or another engine requires a new adapter,
driver, query-validation rules, and tests.

Queries return all matching rows. No application row cap or automatic SQL LIMIT
is applied; an explicitly requested SQL LIMIT is still respected.
