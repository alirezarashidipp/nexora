import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app import conversation
from app.api import app
from app.catalogue import DASHBOARDS, DEMO_DIR, read_dashboards
from app.conversation_plan import Decision, resolve_plan
from app.demo_queries import analyse, check_demo_data, new_plan, open_sources, read_evidence
from app.schema import DEMO_COLUMNS
from build_demo_data import build_databases


class FixturesTest(unittest.TestCase):
    def test_exact_contract_and_reproducible_contents(self):
        expected = {
            "workforce.employees": "employee_id team_id role home_office_id contract_city worker_type fte active",
            "workforce.weekly_schedule": "employee_id week_start mon tue wed thu fri schedule_type",
            "workforce.badge_visits": "visit_id employee_id visited_office_id visit_date check_in check_out visit_type source",
            "locations.offices": "office_id office_name city_code country_code timezone seat_capacity floor_area_sqm monthly_rent_gbp",
            "locations.dashboard_catalog": "dashboard_id title description kpi_tags_json source_tables_json dashboard_url url_status keywords_json",
            "portfolio.portfolio_projects": "project_id project_name team_id lead_office_id required_fte assigned_fte due_date priority",
            "portfolio.hiring_requests": "request_id project_id team_id office_id role open_fte expected_start_date status",
            "facilities.office_daily_metrics": "office_id metric_date onsite_headcount peak_occupancy energy_kwh energy_cost_gbp other_cost_gbp quality_flag",
            "external.weather_daily": "city_code date temp_c rain_mm condition alert_level source recorded_at",
            "external.holiday_calendar": "office_id holiday_date holiday_name holiday_type country_code region_code office_closed source",
        }
        self.assertEqual({k: " ".join(v) for k, v in DEMO_COLUMNS.items()}, expected)
        check_demo_data()
        with tempfile.TemporaryDirectory() as tmp:
            rebuilt = Path(tmp) / "rebuilt"
            build_databases(rebuilt)
            self.assertEqual(len(list(rebuilt.glob("*.db"))), 5)
            for asset in rebuilt.glob("*.db"):
                with closing(sqlite3.connect(asset)) as fresh, closing(sqlite3.connect(DEMO_DIR / asset.name)) as shipped:
                    self.assertEqual(list(fresh.iterdump()), list(shipped.iterdump()))
        with open_sources(DEMO_DIR, DEMO_COLUMNS) as db:
            for row in db.execute("SELECT * FROM facilities.office_daily_metrics"):
                visits = db.execute("SELECT COUNT(DISTINCT employee_id) FROM workforce.badge_visits "
                                    "WHERE visited_office_id=? AND visit_date=?", (row["office_id"], row["metric_date"]))
                self.assertEqual(row["onsite_headcount"], visits.fetchone()[0])
            for row in db.execute("SELECT team_id, SUM(assigned_fte) FROM portfolio.portfolio_projects GROUP BY team_id"):
                capacity = db.execute("SELECT SUM(fte) FROM workforce.employees WHERE active=1 AND team_id=?", (row[0],))
                self.assertAlmostEqual(row[1], capacity.fetchone()[0])

    def test_all_catalogued_measures_and_optional_joins(self):
        self.assertEqual(len(read_dashboards()), 10)
        for key, report in DASHBOARDS.items():
            for metric in report.measures:
                with self.subTest(metric=metric):
                    plan = new_plan(metric)
                    plan.group_by = "city"
                    result = analyse(key, plan)
                    self.assertTrue(result["rows"])
                    self.assertTrue(result["unit"])
        plan = new_plan("visits")
        self.assertEqual(analyse("visits", plan)["sources"], ["workforce.badge_visits"])
        plan.role = "Engineer"
        self.assertIn("workforce.employees", analyse("visits", plan)["sources"])
        plan = new_plan("open_fte")
        plan.group_by = "project"
        self.assertIn("portfolio.portfolio_projects", analyse("hiring", plan)["sources"])
        plan = new_plan("rain_attendance")
        result = analyse("weather", plan)
        self.assertEqual(len(result["sources"]), 3)
        with open_sources(DEMO_DIR, DEMO_COLUMNS) as db:
            rain = {(r[0], r[1]) for r in db.execute("SELECT city_code,date FROM external.weather_daily WHERE rain_mm>0")}
            counts = db.execute("SELECT office_id,metric_date,onsite_headcount FROM facilities.office_daily_metrics")
            expected = sum(n for site, day, n in counts if (site, day) in rain)
        self.assertEqual(result["rows"], [{"value": expected}])

    def test_read_only_scope_and_invalid_plans(self):
        with self.assertRaises(sqlite3.DatabaseError):
            read_evidence("SELECT employee_id FROM workforce.employees", [], ["workforce.weekly_schedule"], DEMO_DIR)
        with self.assertRaises(ValueError):
            read_evidence("DELETE FROM workforce.employees", [], ["workforce.employees"], DEMO_DIR)
        with self.assertRaises(ValueError):
            read_evidence("SELECT 1", [], ["workforce.employees"], DEMO_DIR)
        plan = new_plan("seats")
        plan.start_date = "2026-11-16"
        with self.assertRaisesRegex(ValueError, "snapshot"):
            analyse("estate", plan)
        plan = new_plan("energy")
        plan.team = "Delivery"
        with self.assertRaisesRegex(ValueError, "team"):
            analyse("cost", plan)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, "Missing demo asset"):
                analyse("estate", new_plan("seats"), Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_configured_dashboard_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "urls.json"
            config.write_text(json.dumps({"workforce": "https://example.org/custom"}))
            with patch.dict(os.environ, {"NEXORA_DASHBOARD_URLS_FILE": str(config)}):
                result = {r["dashboard_id"]: r for r in read_dashboards()}
                self.assertEqual(result["workforce"]["url_status"], "configured")
                self.assertEqual(result["workforce"]["dashboard_url"], "https://example.org/custom")
                self.assertEqual(result["estate"]["url_status"], "placeholder")
                config.write_text(json.dumps({"workforce": "file:///unknown"}))
                with self.assertRaises(ValueError):
                    read_dashboards()


class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.thread = str(uuid4())
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def events(self, action, question="", thread=None, run=None, dashboard="", state=None):
        payload = {
            "threadId": thread or self.thread, "runId": run or str(uuid4()),
            "messages": [{"id": "q", "role": "user", "content": question}] if question else [],
            "state": state or {}, "forwardedProps": {"action": action, "dashboard": dashboard},
        }
        response = self.client.post("/ag-ui", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream", response.headers["content-type"])
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    def turn(self, action, question="", **options):
        events = self.events(action, question, **options)
        self.assertEqual([e["type"] for e in events], ["RUN_STARTED", "STATE_SNAPSHOT", "RUN_FINISHED"], events)
        return events[1]["snapshot"]

    def test_discovery_reads_only_metadata_then_answer_and_compare(self):
        first = "How many active employees are in Bristol?"
        with patch("app.catalogue.table_definition", side_effect=AssertionError), \
             patch("app.demo_queries.table_definition", side_effect=AssertionError), \
             patch("app.conversation_plan.llm_connection", side_effect=AssertionError):
            discovery = self.turn("discover", first)
            self.assertEqual([d["dashboard_id"] for d in discovery["dashboards"]], ["workforce"])
            selected = self.turn("select", dashboard="workforce")
            self.assertEqual(selected["selected"], "workforce")
        result = self.turn("answer_here", "Different question", state={"selected": "hiring"})
        self.assertEqual(result["original_question"], first)
        self.assertEqual(result["evidence"]["rows"], [{"value": 3}])
        run = str(uuid4())
        comparison = self.turn("followup", "Compare it with London.", run=run)
        evidence = comparison["evidence"]
        self.assertEqual(evidence["rows"], [{"city": "BRS", "value": 3}, {"city": "LON", "value": 4}])
        self.assertEqual(evidence["parameters"], ["BRS", "LON"])
        self.assertEqual(self.turn("followup", "Compare it with London.", run=run), comparison)
        self.assertEqual(self.turn("restore"), comparison)
        self.assertEqual(self.turn("restore", thread=str(uuid4()))["stage"], "new")

    def test_private_wording_and_failed_run_rollback(self):
        question = "What was the energy cost in Bristol for person@example.org?"
        state = self.turn("discover", question)
        self.assertTrue(state["needs_original"])
        self.assertNotIn("person@", json.dumps(state))
        self.assertEqual(self.events("answer_here", "Another question")[-1]["type"], "RUN_ERROR")
        self.assertEqual(self.turn("restore"), state)
        self.turn("select", dashboard="cost")
        answer = Decision(plan=None, clarification="Which city should I use?")
        with patch("app.conversation.resolve_plan", return_value=answer) as plan:
            self.turn("answer_here", question)
            self.assertEqual(plan.call_args.args[0], question)
        config = {"configurable": {"thread_id": self.thread}}
        checkpoints = list(conversation.saver.list(config))
        self.assertEqual(len(checkpoints), 1)
        for word in ("person@", "SELECT ", "dashboard_url", "possible_values"):
            self.assertNotIn(word, str(checkpoints[0].checkpoint))

    def test_answer_here_can_find_a_measure_without_a_dashboard_hit(self):
        original = "How much floor space is there in Bristol?"
        discovery = self.turn("discover", original)
        self.assertEqual(discovery["dashboards"], [])
        plan = new_plan("floor_area")
        plan.cities = ["Bristol"]
        reply = Decision(plan=plan, clarification=None).model_dump_json()
        completion = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(refusal=None, content=reply))])
        with patch("app.conversation_plan.llm_connection", return_value=nullcontext((object(), "demo"))), \
             patch("app.conversation_plan.create_completion", return_value=completion) as provider:
            answer = self.turn("answer_here")
        payload = provider.call_args.args[3]
        self.assertEqual(payload["question"], original)
        self.assertEqual(set(payload["relevant_schema"]["tables"]), {"locations.offices"})
        self.assertEqual(answer["evidence"]["rows"], [{"value": 108.0}])
        self.assertEqual(answer["evidence"]["unit"], "m²")

    def test_unknown_subject_clarifies_without_touching_sources(self):
        self.turn("discover", "How many?")
        with patch("app.conversation.analyse", side_effect=AssertionError):
            result = self.turn("answer_here")
        self.assertEqual(result["stage"], "clarify")
        self.assertIsNone(result["evidence"])

    def test_bounded_checkpoints_history_and_eviction(self):
        first = "How many active employees are in Bristol?"
        with patch.object(conversation, "MAX_THREADS", 2):
            # Use an isolated LRU and real checkpointer threads for this eviction test.
            with patch.object(conversation, "threads", type(conversation.threads)()):
                self.turn("discover", first)
                self.turn("answer_here")
                for _ in range(8):
                    latest = self.turn("followup", "What about Manchester?")
                self.assertEqual(len(latest["history"]), conversation.HISTORY_SIZE)
                self.assertEqual(latest["original_question"], first)
                cfg = {"configurable": {"thread_id": self.thread}}
                saved = list(conversation.saver.list(cfg))
                self.assertEqual(len(saved), 1)
                values = saved[0].checkpoint["channel_values"]
                self.assertFalse({"sql", "rows", "schema", "evidence"} & values.keys())
                for _ in range(2):
                    self.turn("discover", first, thread=str(uuid4()))
                self.assertEqual(self.turn("restore")["stage"], "new")
                self.assertEqual(self.events("answer_here")[-1]["type"], "RUN_ERROR")

    def test_progressive_schema_and_followup_filters(self):
        previous = new_plan("attendance")
        previous.cities = ["Bristol"]
        previous.start_date = "2026-11-17"
        memory = {"plan": previous.model_dump(), "plan_dashboard": "occupancy",
                  "sources": ["facilities.office_daily_metrics", "locations.offices"]}
        decision = Decision(plan=new_plan("rain_attendance"), clarification=None)
        with patch("app.conversation_plan.llm_connection", return_value=nullcontext((object(), "demo"))), \
             patch("app.conversation_plan.create_completion") as completion, \
             patch("app.conversation_plan.parse_output", return_value=decision):
            resolve_plan("Only rainy days please", "weather", memory)
        supplied = completion.call_args.args[3]
        self.assertEqual(set(supplied["relevant_schema"]["tables"]), {"external.weather_daily"})
        self.assertEqual(supplied["previous_plan"]["start_date"], "2026-11-17")
        result = resolve_plan("Compare it with London", "occupancy", memory)
        self.assertEqual(result.plan.cities, ["Bristol", "London"])
        self.assertEqual(result.plan.start_date, "2026-11-17")
        previous.cities = []
        memory["plan"] = previous.model_dump()
        result = resolve_plan("Compare it with London", "occupancy", memory)
        self.assertIsNone(result.plan)
        self.assertIn("Which other city", result.clarification)
        memory["pending"] = "Compare it with London"
        result = resolve_plan("Manchester", "occupancy", memory)
        self.assertEqual(result.plan.cities, ["Manchester", "London"])
