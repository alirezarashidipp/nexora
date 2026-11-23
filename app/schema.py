TABLE_NAME = "data"

# Office code, country, IANA timezone and region in the synthetic portfolio.
OFFICES = {
    "London": ("LON", "GB", "Europe/London", "ENG"),
    "Manchester": ("MAN", "GB", "Europe/London", "ENG"),
    "Dubai": ("DXB", "AE", "Asia/Dubai", "DU"),
    "Mumbai": ("BOM", "IN", "Asia/Kolkata", "MH"),
    "Bengaluru": ("BLR", "IN", "Asia/Kolkata", "KA"),
    "Shanghai": ("SHA", "CN", "Asia/Shanghai", "SH"),
}
CITIES = {city: site[0] for city, site in OFFICES.items()}
office_codes = ", ".join(CITIES.values())


# The five conversation databases are prebuilt fixtures, separate from /ask.
def column(data_type, description, aliases, possible_values, unit=""):
    return dict(type=data_type, description=description, aliases=aliases,
                possible_values=possible_values, unit=unit)


employee_id = column("INTEGER", "Fictional worker key.", ["worker id"], "1 to 12")
office_id = column("TEXT", "Key of a physical office.", ["site id"], office_codes)
city_code = column("TEXT", "City identifier.", ["city"], office_codes)
team_id = column("TEXT", "Delivery team identifier.", ["team"], "Platform, Delivery, Insights")
job_role = column("TEXT", "Job family.", ["job", "position"], "Engineer, Analyst, Planner")
project_id = column("TEXT", "Portfolio project key.", ["project"], "P01 to P06")
country = column("TEXT", "ISO country code.", ["country"], "GB, AE, IN, CN")
day = column("TEXT", "Local calendar date.", ["date", "day"], "ISO YYYY-MM-DD", "day")
onsite = column("INTEGER", "Planned attendance on this weekday.", ["office day"], "0 = offsite, 1 = onsite", "day")
fte = column("REAL", "Full-time equivalent capacity.", ["full-time equivalent"], "Non-negative decimal", "FTE")

# Column order is also the provisioned SQLite contract.
DEMO_COLUMNS = {
    "workforce.employees": {
        "employee_id": employee_id, "team_id": team_id, "role": job_role,
        "home_office_id": column("TEXT", "Office of assignment.", ["home site"], office_codes),
        "contract_city": column("TEXT", "City on the work contract.", ["contract location"], ", ".join(CITIES)),
        "worker_type": column("TEXT", "Engagement type.", ["employment type"], "Employee, Contractor"),
        "fte": fte, "active": column("INTEGER", "Worker included in the current workforce snapshot.", ["active worker"], "0 = inactive, 1 = active"),
    },
    "workforce.weekly_schedule": {
        "employee_id": employee_id, "week_start": column("TEXT", "Monday starting the schedule week.", ["week"], "ISO Monday", "day"),
        "mon": onsite, "tue": onsite, "wed": onsite, "thu": onsite, "fri": onsite,
        "schedule_type": column("TEXT", "Agreed working pattern.", ["work pattern"], "Hybrid, Remote"),
    },
    "workforce.badge_visits": {
        "visit_id": column("INTEGER", "Unique building visit.", ["entry id"], "Positive integer"),
        "employee_id": employee_id, "visited_office_id": column("TEXT", "Office actually entered.", ["visited site"], office_codes),
        "visit_date": day, "check_in": column("TEXT", "Local arrival time.", ["arrival"], "HH:MM"),
        "check_out": column("TEXT", "Local departure time.", ["departure"], "HH:MM"),
        "visit_type": column("TEXT", "Visit relative to home office.", ["visit category"], "Home, Visiting"),
        "source": column("TEXT", "Synthetic access system.", ["badge system"], "Demo badge reader"),
    },
    "locations.offices": {
        "office_id": office_id, "office_name": column("TEXT", "Display name of the office.", ["site"], ", ".join(CITIES)),
        "city_code": city_code, "country_code": country,
        "timezone": column("TEXT", "IANA office time zone.", ["time zone"], ", ".join(dict.fromkeys(site[2] for site in OFFICES.values()))),
        "seat_capacity": column("INTEGER", "Available workstations.", ["desks", "seats"], "Positive integer", "seats"),
        "floor_area_sqm": column("REAL", "Internal floor area.", ["floor space"], "Positive decimal", "m²"),
        "monthly_rent_gbp": column("REAL", "Contracted rent per month.", ["rent"], "Non-negative decimal", "GBP/month"),
    },
    "locations.dashboard_catalog": {
        "dashboard_id": column("TEXT", "Stable catalogue key.", ["report id"], "One of ten configured slugs"),
        "title": column("TEXT", "Dashboard display title.", ["report name"], "Fictional dashboard title"),
        "description": column("TEXT", "Business question covered.", ["purpose"], "Catalogue text"),
        "kpi_tags_json": column("TEXT", "Supported measure names.", ["KPIs"], "JSON string array"),
        "source_tables_json": column("TEXT", "Qualified source table names.", ["lineage"], "JSON string array"),
        "dashboard_url": column("TEXT", "Configured dashboard destination.", ["link"], "HTTPS URL"),
        "url_status": column("TEXT", "Link configuration status.", ["link status"], "placeholder, configured"),
        "keywords_json": column("TEXT", "Search vocabulary.", ["aliases"], "JSON string array"),
    },
    "portfolio.portfolio_projects": {
        "project_id": project_id, "project_name": column("TEXT", "Fictional portfolio initiative.", ["initiative"], "Harbour, Meridian, Orchard, Copper, Beacon, Willow"),
        "team_id": team_id, "lead_office_id": column("TEXT", "Office accountable for delivery.", ["lead site"], office_codes),
        "required_fte": column("REAL", "Capacity needed to deliver the project.", ["demand"], "Non-negative decimal", "FTE"),
        "assigned_fte": column("REAL", "Capacity already allocated to the project.", ["allocation"], "Non-negative decimal", "FTE"),
        "due_date": column("TEXT", "Planned project delivery date.", ["deadline"], "ISO YYYY-MM-DD", "day"),
        "priority": column("TEXT", "Delivery priority.", ["urgency"], "High, Medium, Low"),
    },
    "portfolio.hiring_requests": {
        "request_id": column("TEXT", "Hiring requisition key.", ["requisition"], "H01 to H06"),
        "project_id": project_id, "team_id": team_id, "office_id": office_id, "role": job_role,
        "open_fte": column("REAL", "Remaining unfilled capacity on the requisition.", ["vacancy"], "Non-negative decimal", "FTE"),
        "expected_start_date": column("TEXT", "Planned start date for the hire.", ["start"], "ISO YYYY-MM-DD", "day"),
        "status": column("TEXT", "Requisition state.", ["hiring status"], "Open, Filled"),
    },
    "facilities.office_daily_metrics": {
        "office_id": office_id, "metric_date": day,
        "onsite_headcount": column("INTEGER", "Distinct people onsite that day.", ["daily attendance"], "0 to seat capacity", "people"),
        "peak_occupancy": column("INTEGER", "Maximum simultaneous occupants that day.", ["peak attendance"], "0 to daily headcount", "people"),
        "energy_kwh": column("REAL", "Electricity consumed that day.", ["energy use"], "Non-negative decimal", "kWh"),
        "energy_cost_gbp": column("REAL", "Daily electricity charge.", ["energy cost"], "Non-negative decimal", "GBP"),
        "other_cost_gbp": column("REAL", "Daily operating costs excluding energy and rent.", ["other costs"], "Non-negative decimal", "GBP"),
        "quality_flag": column("TEXT", "Measurement quality.", ["data quality"], "Verified"),
    },
    "external.weather_daily": {
        "city_code": city_code, "date": day, "temp_c": column("REAL", "Mean daily air temperature.", ["temperature"], "Decimal degrees Celsius", "°C"),
        "rain_mm": column("REAL", "Daily precipitation.", ["rainfall"], "Non-negative decimal", "mm"),
        "condition": column("TEXT", "Daily weather category.", ["weather"], "Clear, Rain"),
        "alert_level": column("TEXT", "Local weather warning.", ["warning"], "None, Yellow"),
        "source": column("TEXT", "Weather provider.", ["provider"], "Demo weather"),
        "recorded_at": column("TEXT", "Observation timestamp in UTC.", ["observation time"], "ISO 8601 UTC"),
    },
    "external.holiday_calendar": {
        "office_id": office_id, "holiday_date": day,
        "holiday_name": column("TEXT", "Fictional closure or observance.", ["holiday"], "Demo office day"),
        "holiday_type": column("TEXT", "Kind of calendar event.", ["event type"], "Local, Observance"),
        "country_code": country, "region_code": column("TEXT", "Office region.", ["region"], "ENG, DU, MH, KA, SH"),
        "office_closed": column("INTEGER", "Office unavailable on this date.", ["closure"], "0 = open, 1 = closed"),
        "source": column("TEXT", "Calendar owner.", ["calendar source"], "Demo calendar"),
    },
}

DEMO_KEYS = {
    "workforce.employees": ("employee_id",), "workforce.weekly_schedule": ("employee_id", "week_start"),
    "workforce.badge_visits": ("visit_id",), "locations.offices": ("office_id",),
    "locations.dashboard_catalog": ("dashboard_id",), "portfolio.portfolio_projects": ("project_id",),
    "portfolio.hiring_requests": ("request_id",), "facilities.office_daily_metrics": ("office_id", "metric_date"),
    "external.weather_daily": ("city_code", "date"), "external.holiday_calendar": ("office_id", "holiday_date"),
}

DEMO_EXAMPLES = (
    "How many active employees are in Dubai?",
    "What was the energy cost in Manchester?",
    "What was rainy-day attendance in Mumbai?",
    "What is the project staffing gap in Shanghai?",
)


COLUMN_GUIDE = {
    "EMPLOYEE_ID": {
        "description": "Unique numeric identifier for one employee.",
        "possible_values": "Integer IDs from 1001 to 1100.",
    },
    "EMPLOYEE_NAME": {
        "description": "Synthetic full name of the employee.",
        "possible_values": (
            "Synthetic employee names."
        ),
    },
    "DEPARTMENT": {
        "description": "Organizational department where the employee works.",
        "possible_values": "MSW, MRM, SECURITY.",
    },
    "STATUS": {
        "description": "Whether the employee is classified as a coding role.",
        "possible_values": "CODER, NON-CODER.",
    },
    "TITLE": {
        "description": "Official job title of the employee.",
        "possible_values": (
            "SOFTWARE ENGINEER, DATA ENGINEER, PROJECT MANAGER, RECRUITER, "
            "SECURITY OFFICER, SECURITY ENGINEER, SECURITY ANALYST, "
            "BUSINESS ANALYST."
        ),
    },
}

EXAMPLE_QUESTIONS = (
    "How many coders?",
    "Data engineers in MRM",
    "How many?",
    "How many employees are in each department?",
)


# Update these golden cases with COLUMN_GUIDE when the configured dataset changes.
EVAL_CASES = {
    "sql": (
        {
            "id": "coder-count",
            "question": "How many coders are there?",
            "reference_sql": (
                f'SELECT COUNT(*) FROM "{TABLE_NAME}" '
                'WHERE "STATUS" = \'CODER\''
            ),
            "compare": "scalar",
        },
        {
            "id": "department-counts",
            "question": "How many employees are in each department?",
            "reference_sql": (
                f'SELECT "DEPARTMENT", COUNT(*) FROM "{TABLE_NAME}" '
                'GROUP BY "DEPARTMENT"'
            ),
            "compare": "grouped",
        },
        {
            "id": "mrm-data-engineers",
            "question": "List the names of data engineers in MRM.",
            "reference_sql": (
                f'SELECT "EMPLOYEE_NAME" FROM "{TABLE_NAME}" '
                'WHERE "TITLE" = \'DATA ENGINEER\' '
                'AND "DEPARTMENT" = \'MRM\''
            ),
            "compare": "unordered",
        },
        {
            "id": "smallest-employee-ids",
            "question": (
                "List the five smallest employee IDs in ascending order."
            ),
            "reference_sql": (
                f'SELECT "EMPLOYEE_ID" FROM "{TABLE_NAME}" '
                'ORDER BY "EMPLOYEE_ID" ASC LIMIT 5'
            ),
            "compare": "ordered",
        },
    ),
    "rejected": (
        {
            "id": "missing-subject",
            "question": "How many?",
            "expected_status": "incomplete",
        },
        {
            "id": "undefined-best",
            "question": "Who are the best employees?",
            "expected_status": "incomplete",
        },
        {
            "id": "missing-column",
            "question": "What is the average employee salary?",
            "expected_status": "invalid",
        },
        {
            "id": "write-request",
            "question": "Delete every employee.",
            "expected_status": "invalid",
        },
    ),
    "answers": (
        {
            "id": "scalar-answer",
            "question": "How many employees are there?",
            "sql": f'SELECT COUNT(*) AS total FROM "{TABLE_NAME}"',
            "rows": [{"total": 3}],
            "reference_answer": "There are 3 employees.",
        },
        {
            "id": "grouped-answer",
            "question": "How many employees are in each department?",
            "sql": (
                f'SELECT "DEPARTMENT", COUNT(*) AS total FROM "{TABLE_NAME}" '
                'GROUP BY "DEPARTMENT"'
            ),
            "rows": [
                {"DEPARTMENT": "MRM", "total": 2},
                {"DEPARTMENT": "SECURITY", "total": 1},
            ],
            "reference_answer": (
                "MRM has 2 employees and SECURITY has 1 employee."
            ),
        },
    ),
}
