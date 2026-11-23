import json
import os
import sqlite3
import socket
import tempfile
import threading
import time
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import uvicorn
from fastapi.testclient import TestClient
from openai import OpenAI

from app import conversation
from app.api import app
from app.catalogue import DEMO_DIR, find_dashboards, read_dashboards
from app.demo_queries import METRIC_GROUPS, Plan, analyse, check_demo_data, open_sources, read_evidence
from app.llm_connection import LLMConfigurationError
from app.prompts import load_prompt
from app.schema import DEMO_COLUMNS
from build_demo_data import build_databases


def new_plan(metric: str) -> Plan:
    return Plan(metric=metric, group_by="total", cities=[], team=None, role=None, start_date=None, end_date=None)


def model_match(key, coverage="exact", gap="none", reason=None):
    card = next(item for item in read_dashboards() if item["dashboard_id"] == key)
    kpi = card["kpi_tags"][0]
    return dict(dashboard_id=key, coverage=coverage, kpi=kpi, gap=gap, dimensions=[], requested_offices=[],
                reason=reason or card["description"])


class FixturesTest(unittest.TestCase):
    def test_six_agreed_sites_across_all_five_databases(self):
        expected = {"London": ("LON", "GB", "Europe/London", "ENG"),
                    "Manchester": ("MAN", "GB", "Europe/London", "ENG"),
                    "Dubai": ("DXB", "AE", "Asia/Dubai", "DU"),
                    "Mumbai": ("BOM", "IN", "Asia/Kolkata", "MH"),
                    "Bengaluru": ("BLR", "IN", "Asia/Kolkata", "KA"),
                    "Shanghai": ("SHA", "CN", "Asia/Shanghai", "SH")}
        codes = {details[0] for details in expected.values()}
        with open_sources(DEMO_DIR, DEMO_COLUMNS) as db:
            rows = db.execute("SELECT office_name, city_code, offices.country_code, timezone, region_code "
                              "FROM locations.offices JOIN external.holiday_calendar USING (office_id, country_code)")
            self.assertEqual({r[0]: tuple(r[1:]) for r in rows}, expected)
            checks = ["SELECT DISTINCT home_office_id FROM workforce.employees",
                      "SELECT DISTINCT home_office_id FROM workforce.weekly_schedule JOIN workforce.employees USING (employee_id)",
                      "SELECT DISTINCT visited_office_id FROM workforce.badge_visits",
                      "SELECT DISTINCT lead_office_id FROM portfolio.portfolio_projects",
                      "SELECT DISTINCT office_id FROM portfolio.hiring_requests",
                      "SELECT DISTINCT office_id FROM facilities.office_daily_metrics",
                      "SELECT DISTINCT city_code FROM external.weather_daily",
                      "SELECT DISTINCT office_id FROM external.holiday_calendar"]
            for sql in checks:
                self.assertEqual({r[0] for r in db.execute(sql)}, codes, sql)

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
        self.assertEqual(len(read_dashboards()), 6)
        for key, report in METRIC_GROUPS.items():
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

    def test_fixture_dashboard_mappings_follow_published_yaml(self):
        card = read_dashboards()[0]
        card.pop("url_status")
        card["dashboard_id"] = "office_capacity_review"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboards.yml"
            assets = Path(directory) / "assets"
            path.write_text(json.dumps([card]), encoding="utf-8")
            with patch("app.catalogue.CATALOGUE_PATH", path):
                build_databases(assets)
                card["dashboard_id"] = "regional_capacity_review"
                path.write_text(json.dumps([card]), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "published catalogue"):
                    check_demo_data(assets)

    def test_read_only_scope_and_invalid_plans(self):
        with open_sources(DEMO_DIR, ["workforce.employees"]) as db:
            self.assertEqual(db.execute("PRAGMA query_only").fetchone()[0], 1)
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
            config.write_text(json.dumps({"workforce": "https://reports.internal/custom"}))
            with patch.dict(os.environ, {"NEXORA_DASHBOARD_URLS_FILE": str(config)}):
                result = {r["dashboard_id"]: r for r in read_dashboards()}
                self.assertEqual(result["workforce"]["url_status"], "configured")
                self.assertEqual(result["workforce"]["dashboard_url"], "https://reports.internal/custom")
                for key, card in result.items():
                    if key != "workforce":
                        self.assertEqual(card["url_status"], "sample")
                        self.assertEqual(card["dashboard_url"], f"https://example.org/#{key}")
                config.write_text(json.dumps({"estate": ""}))
                disabled = next(card for card in read_dashboards() if card["dashboard_id"] == "estate")
                self.assertEqual(disabled["url_status"], "placeholder")
                self.assertIsNone(disabled["dashboard_url"])
                config.write_text(json.dumps({"workforce": "https://reports.example.org/workforce"}))
                placeholder = next(card for card in read_dashboards() if card["dashboard_id"] == "workforce")
                self.assertIsNone(placeholder["dashboard_url"])
                config.write_text(json.dumps({"workforce": "file:///unknown", "estate": "https://reports.internal/estate"}))
                cards = {r["dashboard_id"]: r for r in read_dashboards()}
                self.assertEqual(cards["workforce"]["url_status"], "unavailable")
                self.assertIsNone(cards["workforce"]["dashboard_url"])
                self.assertEqual(cards["estate"]["dashboard_url"], "https://reports.internal/estate")
                config.write_text("unreadable URL mapping")
                self.assertTrue(all(r["url_status"] == "unavailable" for r in read_dashboards()))


class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.thread = str(uuid4())
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.requests = []
        self.replies = []
        self.provider = OpenAI(api_key="offline-test", max_retries=0,
                               http_client=httpx.Client(transport=httpx.MockTransport(self.respond)))
        self.addCleanup(self.provider.close)
        self.enterContext(patch.dict(os.environ, {"LLM_TEMPERATURE": "0"}))
        self.enterContext(patch("app.conversation_plan.llm_connection",
                                return_value=nullcontext((self.provider, "offline-test"))))

    def respond(self, request):
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        self.requests.append((prompt, json.loads(body["messages"][1]["content"])))
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertFalse(body["store"])
        self.assertTrue(self.replies, "Unexpected model request")
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply()
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json={"id": "test", "model": "offline-test", "created": 0,
            "object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(reply)}}]})

    def tearDown(self):
        self.assertEqual(self.replies, [], "Expected provider request did not run")

    def events(self, action, question="", thread=None, run=None, dashboard="", state=None,
               prior_question="", original_token="", clarification=""):
        payload = {"threadId": thread or self.thread, "runId": run or str(uuid4()),
                   "messages": [{"id": "q", "role": "user", "content": question}] if question else [],
                   "state": state or {}, "forwardedProps": {"action": action, "dashboard": dashboard,
                                                          "prior_question": prior_question, "original_token": original_token,
                                                          "clarification": clarification}}
        response = self.client.post("/ag-ui", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream", response.headers["content-type"])
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    def turn(self, action, question="", **options):
        events = self.events(action, question, **options)
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED", events[-1])
        self.assertNotIn("original_digest", json.dumps(events))
        snapshots = [e["snapshot"] for e in events if e["type"] == "STATE_SNAPSHOT"]
        return snapshots[-1]

    def scope(self, table, *joins, offices=()):
        self.replies.append(dict(base=table, joins=list(joins), office_filters=list(offices), clarification=None))

    def metric(self, key, cities, group="total", start=None, end=None):
        plan = new_plan(key)
        plan.cities, plan.group_by, plan.start_date = cities, group, start
        plan.end_date = end
        self.replies.append(dict(plan=plan.model_dump(), fields=None, clarification=None))

    def fields(self, outputs, filters=(), sort=(), limit=None):
        fields = dict(projections=[dict(column=c, operation=o) for c, o in outputs],
                      filters=[dict(column=c, operator=o, values=v) for c, o, v in filters],
                      sort=[dict(position=i, direction=d) for i, d in sort], distinct=False, limit=limit)
        self.replies.append(dict(plan=None, fields=fields, clarification=None))

    def test_discovery_reads_only_metadata_then_answer_and_compare(self):
        first = "How many active employees are in Dubai?"
        discovery_run = str(uuid4())
        reason = "Shows active employees by home office."
        self.replies.append({"clarification": None, "matches": [model_match("workforce", reason=reason),
                                        model_match("hiring", "related", "measure", "Shows open hiring capacity by hiring office.")]})
        with patch("sqlite3.connect", side_effect=AssertionError("Discovery opened SQLite")), \
             patch("app.catalogue.table_definition", side_effect=AssertionError), \
             patch("app.conversation_plan.find_dashboards", side_effect=AssertionError("Semantic rank reached keyword search")):
            discovery = self.turn("discover", first, run=discovery_run)
            self.assertEqual(discovery["discovery_mode"], "semantic")
            self.assertEqual([d["dashboard_id"] for d in discovery["dashboards"]], ["workforce", "hiring"])
            self.assertEqual([d["coverage"] for d in discovery["dashboards"]], ["exact", "related"])
            self.assertEqual(discovery["dashboards"][0]["reason"], reason)
            self.assertEqual(self.turn("select", state={"selected": "workforce"})["selected"], "workforce")
            self.assertEqual(self.turn("restore")["dashboards"], discovery["dashboards"])
        sent = self.requests[0][1]
        self.assertEqual(set(sent), {"question", "catalogue", "office_locations", "conversation_context"})
        self.assertEqual(len(sent["catalogue"]), 6)
        for entry in sent["catalogue"]:
            self.assertEqual(set(entry), {"dashboard_id", "title", "description", "keywords", "kpi_tags", "dimensions",
                                          "business_purpose", "supported_questions", "source_tables"})
        self.scope("workforce.employees", "employee_office")
        self.metric("employees", ["Dubai"])
        result = self.turn("answer_here", "Other question", state={"selected": "hiring", "plan": {}})
        self.assertEqual(self.requests[-1][1]["question"], first)
        self.assertEqual(self.requests[-1][1]["selected_dashboard"], "workforce")
        self.assertEqual(result["evidence"]["rows"], [{"value": 2}])
        with patch("sqlite3.connect", side_effect=AssertionError("Replayed discovery touched SQLite")):
            replay = self.turn("discover", first, run=discovery_run)
        self.assertEqual(replay["stage"], "discover")
        self.assertEqual(replay["dashboards"], discovery["dashboards"])
        self.assertIsNone(replay["evidence"])
        self.assertEqual(replay["sources"], [])
        self.scope("workforce.employees", "employee_office")
        self.metric("employees", ["Dubai", "London"], "city")
        run = str(uuid4())
        comparison = self.turn("followup", "Compare it with London.", run=run)
        self.assertEqual(comparison["evidence"]["rows"], [{"city": "DXB", "value": 2}, {"city": "LON", "value": 2}])
        self.assertEqual(comparison["evidence"]["parameters"], ["DXB", "LON"])
        self.assertEqual(self.requests[-1][1]["previous_plan"]["metric_plan"]["cities"], ["Dubai"])
        self.assertEqual(self.turn("followup", "Compare it with London.", run=run), comparison)
        self.assertEqual(self.turn("restore"), comparison)
        self.assertEqual(self.turn("restore", thread=str(uuid4()))["stage"], "new")

    def test_workspace_routes_share_one_question_form(self):
        html = self.client.get("/employees").text
        for path in ("/", "/conversation"):
            self.assertEqual(self.client.get(path).text, html)
        self.assertIn('id="answer-here"', html)
        self.assertEqual(self.client.get("/conversation", follow_redirects=False).headers["location"], "/employees")
        self.assertIn("synthetic workplace data", html)
        self.assertEqual(html.count("<form"), 1)
        for removed in ('href="/conversation"', 'id="suggestions"', 'demo-badge'):
            self.assertNotIn(removed, html)

    def test_discovery_uses_current_yaml_and_clarifies_uncovered_office(self):
        question = "How many people are based in Delhi?"
        card = dict(dashboard_id="directory", title="Team directory", description="Headcount by office.",
                    keywords=["staff"], dimensions=["office"], kpi_tags=["Headcount"],
                    business_purpose="Locate staff", supported_questions=["How many staff work here?"],
                    source_tables=["workforce.employees"], dashboard_url="https://reports.internal/directory")
        self.replies.append(dict(clarification=None, matches=[dict(dashboard_id="directory",
            coverage="related", kpi="Headcount", gap="scope", dimensions=["office"], requested_offices=["Delhi"], reason="Shows headcount by office.")]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboards.yml"
            path.write_text(json.dumps([card]), encoding="utf-8")
            with patch("app.catalogue.CATALOGUE_PATH", path), patch("sqlite3.connect", side_effect=AssertionError):
                discovery = self.turn("discover", question)
                expected = {key: value for key, value in card.items() if key != "dashboard_url"}
                self.assertEqual(self.requests[0][1]["catalogue"], [expected])
                self.assertEqual(discovery["dashboards"][0]["dashboard_url"], card["dashboard_url"])
                self.assertEqual(discovery["dashboards"][0]["coverage"], "related")
                result = self.turn("answer_here", question)
                self.assertEqual(len(self.requests), 1)
                self.assertIn("does not cover", result["notice"])
                self.assertEqual(self.turn("restore")["notice"], result["notice"])
                self.assertEqual(result["stage"], "clarify")
                self.assertIsNone(result["evidence"])

    def test_uncovered_office_with_no_dashboard_then_user_changes_city(self):
        question = "How many employees are based in Delhi?"
        self.replies.append(dict(matches=[], clarification=None))
        with patch("sqlite3.connect", side_effect=AssertionError("Unexpected database read")):
            self.turn("discover", question)
            self.scope("workforce.employees", "employee_office", offices=["Delhi"])
            view = self.turn("answer_here", question)
            self.assertEqual(view["stage"], "clarify")
            self.assertIn("does not cover", view["notice"])
            self.assertIsNone(view["evidence"])
        self.scope("workforce.employees", "employee_office", offices=["London"])
        self.metric("employees", ["London"])
        view = self.turn("followup", "Use London instead.", prior_question=question)
        self.assertEqual(view["evidence"]["rows"], [{"value": 2}])
        self.assertEqual(view["evidence"]["parameters"], ["LON"])

    def test_missing_or_substituted_office_filter_never_returns_a_total(self):
        for kind in ("metric", "fields"):
            with self.subTest(kind=kind):
                thread = str(uuid4())
                question = "How many employees are based in London?"
                self.replies.append(dict(matches=[], clarification=None))
                self.turn("discover", question, thread=thread)
                self.scope("workforce.employees", "employee_office", offices=["London"])
                if kind == "metric":
                    self.metric("employees", [])
                else:
                    self.fields([("employees.employee_id", "count")], [("offices.city_code", "=", ["DXB"])])
                with patch("app.conversation.execute_plan", side_effect=AssertionError):
                    view = self.turn("answer_here", question, thread=thread)
                self.assertEqual(view["stage"], "clarify")
                self.assertIsNone(view["evidence"])

    def test_catalogue_rejects_full_schema_in_dashboard_metadata(self):
        card = read_dashboards()[0]
        card.pop("url_status")
        card["columns"] = {"employee_id": "INTEGER"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dashboards.yml"
            path.write_text(json.dumps([card]), encoding="utf-8")
            with patch("app.catalogue.CATALOGUE_PATH", path), patch("sqlite3.connect", side_effect=AssertionError):
                with self.assertRaisesRegex(ValueError, "Invalid dashboard catalogue"):
                    read_dashboards()

    def test_rediscover_in_thread_then_analyse_independent_sources(self):
        first = "How many active employees are in Dubai?"
        self.replies.append(dict(clarification=None, matches=[model_match("workforce")]))
        self.turn("discover", first)
        self.scope("workforce.employees", "employee_office")
        self.metric("employees", ["Dubai"])
        self.turn("answer_here")
        self.assertIsNone(self.requests[-1][1]["selected_dashboard"])

        question = "How warm was it there on average?"
        self.replies.append(dict(clarification=None, matches=[model_match("occupancy", "related", "measure")]))
        with patch("sqlite3.connect", side_effect=AssertionError("Discovery opened SQLite")):
            events = self.events("discover", question)
        self.assertEqual([e["stepName"] for e in events if e["type"] == "STEP_STARTED"],
                         ["prepare_turn", "discover_dashboards"])
        context = self.requests[-1][1]["conversation_context"]
        self.assertEqual(context["recent_questions"], [first])
        self.assertEqual(context["offices"], ["Dubai"])
        self.scope("external.weather_daily")
        self.fields([("weather_daily.temp_c", "avg")], [("weather_daily.city_code", "=", ["DXB"])])
        answer = self.turn("answer_here", question)
        self.assertEqual(self.requests[-1][1]["original_question"], question)
        self.assertIsNone(self.requests[-1][1]["selected_dashboard"])
        self.assertEqual(answer["evidence"]["sources"], ["external.weather_daily"])
        self.assertEqual(answer["evidence"]["parameters"], ["DXB"])
        self.assertEqual(len(answer["history"]), 2)

    def test_paraphrases_can_match_without_any_keyword_hit(self):
        catalog = read_dashboards()
        for question, match in (("Who is on our payroll?", "workforce"),
                                ("Where do we keep burning through power?", "cost")):
            with self.subTest(question=question):
                self.assertEqual(find_dashboards(question, catalog), [])
                self.replies.append({"clarification": None, "matches": [model_match(match)]})
                view = self.turn("discover", question, thread=str(uuid4()))
                self.assertEqual(view["discovery_mode"], "semantic")
                self.assertEqual([d["dashboard_id"] for d in view["dashboards"]], [match])
                self.assertEqual(self.requests[-1][0], load_prompt("dashboard_discovery"))

    def test_validated_recommendations_keep_model_order_and_cap_at_three(self):
        private = "Contact person@example.org for a full cost breakdown."
        first = model_match("cost", "related", "calculation", private)
        self.replies.append({"clarification": None, "matches": [
            {**model_match("estate"), "dashboard_id": "unknown"}, first, first,
            {**model_match("occupancy"), "kpi": "unpublished KPI"},
            model_match("portfolio", "exact", "forecast"),
            {**model_match("estate"), "dimensions": ["surname"]},
            {**model_match("workforce"), "requested_offices": ["Delhi"]},
            model_match("hiring"), model_match("estate"), model_match("workforce"),
        ]})
        with patch("sqlite3.connect", side_effect=AssertionError), \
             patch("app.conversation_plan.find_dashboards", side_effect=AssertionError):
            view = self.turn("discover", "Help me understand the cost of using our buildings.")
        self.assertEqual(view["discovery_mode"], "semantic")
        self.assertEqual([c["dashboard_id"] for c in view["dashboards"]], ["cost", "hiring", "estate"])
        reason = view["dashboards"][0]["reason"]
        self.assertIn("energy use", reason)
        self.assertIn("separate analysis", reason)
        self.assertNotIn("person@", json.dumps(view))
        self.assertNotIn("person@", str(conversation.saver.get_tuple({"configurable": {"thread_id": self.thread}}).checkpoint))
        error = self.events("select", state={"selected": "unknown"})[-1]
        self.assertEqual(error["type"], "RUN_ERROR")

    def test_only_an_unavailable_provider_can_trigger_keyword_matching(self):
        question = "What was the energy cost in Manchester?"
        expected = find_dashboards(question, read_dashboards())
        invalid = {**model_match("cost"), "dashboard_id": "invented"}
        refusal = httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"role": "assistant", "content": None, "refusal": "refused"}}]})
        with patch("app.conversation_plan.find_dashboards", side_effect=AssertionError("Unexpected keyword fallback")):
            for reply in ({"clarification": None, "matches": [invalid]}, {"invalid": True}, refusal, httpx.Response(200, text="invalid"),
                          httpx.Response(400, json={"error": {"message": "rejected"}})):
                self.replies.append(reply)
                view = self.turn("discover", question, thread=str(uuid4()))
                self.assertEqual(view["discovery_mode"], "invalid_ranking")
                self.assertEqual(view["dashboards"], [])
            self.replies.append({"clarification": None, "matches": []})
            view = self.turn("discover", question, thread=str(uuid4()))
            self.assertEqual((view["discovery_mode"], view["dashboards"]), ("semantic", []))
        with patch("app.conversation_plan.llm_connection", side_effect=LLMConfigurationError("Offline")):
            view = self.turn("discover", question)
            self.assertEqual(view["discovery_mode"], "keyword_fallback")
            self.assertEqual([d["dashboard_id"] for d in view["dashboards"]], expected)
            self.assertTrue(all(d["coverage"] == "related" and d["reason"] for d in view["dashboards"]))
            error = self.events("answer_here")[-1]
            self.assertEqual((error["type"], error["code"]), ("RUN_ERROR", "provider"))
            self.assertEqual(self.turn("restore"), view)
        self.replies.append(httpx.Response(503, json={"error": {"message": "temporarily unavailable"}}))
        self.assertEqual(self.turn("discover", question, thread=str(uuid4()))["discovery_mode"], "keyword_fallback")

    def test_every_suggested_question_uses_the_real_planner(self):
        cases = (
            ("estate", "seats", ["London"], "total", None),
            ("workforce", "employees", ["Dubai"], "total", None),
            ("cost", "energy_cost", ["London", "Manchester"], "city", None),
            ("occupancy", "utilisation", [], "city", "2026-11-17"),
            ("weather", "rain_attendance", ["London", "Mumbai"], "city", None),
        )
        examples = self.client.get("/conversation/examples").json()
        self.assertEqual(len(examples), len(cases))
        self.assertEqual(len(set(examples)), 5)
        for question, (report, metric, cities, group, day) in zip(examples, cases):
            thread = str(uuid4())
            self.replies.append({"clarification": None, "matches": [] if report == "weather" else [model_match(report)]})
            self.scope(METRIC_GROUPS[report].table, *METRIC_GROUPS[report].joins, offices=cities)
            self.metric(metric, cities, group, start=day, end=day)
            self.turn("discover", question, thread=thread)
            view = self.turn("answer_here", question, thread=thread)
            self.assertTrue(view["evidence"]["rows"])
            last = self.requests[-3:]
            self.assertEqual([prompt for prompt, _ in last], [load_prompt(n) for n in
                             ("dashboard_discovery", "conversation_scope", "conversation_plan")])
            self.assertTrue(all(body["question"] == question for _, body in last))

    def test_unseen_ranked_question_outside_dashboard_metrics(self):
        question = "Rank the three biggest workplaces by indoor area."
        self.replies.append({"clarification": None, "matches": []})
        self.assertFalse(self.turn("discover", question)["dashboards"])
        self.scope("locations.offices")
        self.fields([("offices.office_name", "field"), ("offices.floor_area_sqm", "field")],
                    sort=[(2, "desc")], limit=3)
        events = self.events("answer_here", question)
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        steps = [e["stepName"] for e in events if e["type"] == "STEP_STARTED"]
        self.assertEqual(steps, ["prepare_turn", "select_sources", "plan_query", "query_sources"])
        view = [e["snapshot"] for e in events if e["type"] == "STATE_SNAPSHOT"][-1]
        self.assertEqual(view["evidence"]["rows"], [{"office_name": c, "floor_area_sqm": a} for c, a in
                                               [("Shanghai", 156), ("Bengaluru", 144), ("Mumbai", 132)]])
        self.assertEqual(view["evidence"]["parameters"], [3])
        self.assertEqual(set(self.requests[-1][1]["relevant_schema"]["tables"]), {"locations.offices"})

    def test_progressive_schema_and_followup_filters(self):
        first = "What was attendance in Mumbai from 2026-11-17?"
        self.replies.append({"clarification": None, "matches": [model_match("occupancy")]})
        self.turn("discover", first)
        self.scope("facilities.office_daily_metrics", "metrics_office")
        self.metric("attendance", ["Mumbai"], start="2026-11-17")
        self.turn("answer_here")
        self.scope("facilities.office_daily_metrics", "metrics_office", "office_weather")
        self.metric("rain_attendance", ["Mumbai"], start="2026-11-17")
        view = self.turn("followup", "Only on days when it rained.")
        request = self.requests[-1][1]
        self.assertEqual(set(request["relevant_schema"]["tables"]), {"external.weather_daily"})
        self.assertEqual(set(request["previously_loaded_columns"]), {"locations.offices", "facilities.office_daily_metrics"})
        self.assertEqual(request["previous_plan"]["metric_plan"]["start_date"], "2026-11-17")
        evidence = view["evidence"]
        self.assertIn("office_daily_metrics.metric_date = weather_daily.date", evidence["sql"])
        self.assertEqual(evidence["parameters"], ["BOM", "2026-11-17"])
        self.assertEqual(self.turn("restore")["evidence"], evidence)

    def test_unseen_weather_question_and_contextual_custom_comparison(self):
        first = "How warm was Shanghai on average?"
        self.replies.append({"clarification": None, "matches": []})
        self.turn("discover", first)
        self.scope("external.weather_daily")
        self.fields([("weather_daily.temp_c", "avg")], [("weather_daily.city_code", "=", ["SHA"])])
        view = self.turn("answer_here", first)
        self.assertEqual(view["evidence"]["rows"], [{"avg_temp_c": 14.5}])
        self.assertEqual(view["evidence"]["unit"], "°C")
        self.scope("external.weather_daily")
        self.fields([("weather_daily.city_code", "field"), ("weather_daily.temp_c", "avg")],
                    [("weather_daily.city_code", "in", ["SHA", "LON"])], sort=[(1, "asc")])
        view = self.turn("followup", "And how does that stack up against London?")
        self.assertEqual(view["evidence"]["rows"], [{"city_code": "LON", "avg_temp_c": 9.5},
                                                  {"city_code": "SHA", "avg_temp_c": 14.5}])
        for word in ("London", "9.5", "Shanghai", "14.5"):
            self.assertIn(word, view["evidence"]["answer"])
        request = self.requests[-1][1]
        self.assertEqual(request["previous_plan"]["fields"]["filters"][0]["values"], ["SHA"])
        self.assertEqual(request["relevant_schema"]["tables"], {})

    def test_private_wording_and_failed_run_rollback(self):
        question = "What was the energy cost in Dubai for person@example.org?"
        self.replies.append({"clarification": None, "matches": [model_match("cost")]})
        state = self.turn("discover", question)
        self.assertTrue(state["needs_original"])
        self.assertNotIn("person@", json.dumps(state))
        self.assertEqual(self.events("answer_here", "Another question")[-1]["type"], "RUN_ERROR")
        self.assertEqual(self.turn("restore"), state)
        self.turn("select", state={"selected": "cost"})
        self.replies.append(dict(base=None, joins=[], office_filters=[], clarification="Which city should I use?"))
        self.turn("answer_here", question)
        self.assertEqual(self.requests[-1][1]["question"], question)
        saved = list(conversation.saver.list({"configurable": {"thread_id": self.thread}}))
        self.assertEqual(len(saved), 1)
        for word in ("person@", "SELECT ", "dashboard_url", "possible_values"):
            self.assertNotIn(word, str(saved[0].checkpoint))

    def test_original_can_be_used_after_reload_without_resending_the_text(self):
        wording = "How warm was it beside the Shanghai symposium?"
        self.replies.append({"clarification": None, "matches": []})
        events = self.events("discover", wording)
        receipt = next(e["value"] for e in events if e.get("name") == "original_question_token")
        self.assertNotIn("symposium", json.dumps(events))
        self.assertTrue(self.turn("restore")["needs_original"])
        self.assertEqual(self.events("answer_here", original_token="invalid")[-1]["code"], "question")
        other = str(uuid4())
        self.replies.append({"clarification": None, "matches": []})
        self.turn("discover", wording, thread=other)
        self.assertEqual(self.events("answer_here", thread=other, original_token=receipt)[-1]["code"], "question")
        self.scope("external.weather_daily")
        self.fields([("weather_daily.temp_c", "avg")], [("weather_daily.city_code", "=", ["SHA"])])
        answer = self.turn("answer_here", original_token=receipt)
        self.assertEqual(self.requests[-1][1]["question"], wording)
        self.assertEqual(answer["evidence"]["rows"], [{"avg_temp_c": 14.5}])
        saved = conversation.saver.get_tuple({"configurable": {"thread_id": self.thread}})
        self.assertNotIn(receipt, str(saved.checkpoint))
        self.assertNotIn("symposium", str(saved.checkpoint))

    def test_missing_dataset_still_allows_discovery_and_a_retry(self):
        first = "What is the average temperature in Shanghai?"
        self.replies.append({"clarification": None, "matches": []})
        with tempfile.TemporaryDirectory() as tmp, patch("app.catalogue.DEMO_DIR", Path(tmp)):
            discovery = self.turn("discover", first)
            with patch("app.conversation.choose_sources", side_effect=AssertionError):
                self.assertEqual(self.events("followup", "Start now")[-1]["code"], "question")
            self.scope("external.weather_daily")
            failure = self.events("answer_here", first)[-1]
            self.assertEqual(failure["code"], "dataset")
            self.assertIn("external", failure["message"])
            self.assertEqual(self.turn("restore"), discovery)
            with closing(sqlite3.connect(Path(tmp) / "external.db")):
                pass
            self.scope("external.weather_daily")
            self.assertEqual(self.events("answer_here", first)[-1]["code"], "dataset")
        self.scope("external.weather_daily")
        self.fields([("weather_daily.temp_c", "avg")], [("weather_daily.city_code", "=", ["SHA"])])
        self.assertEqual(self.turn("answer_here", first)["evidence"]["rows"], [{"avg_temp_c": 14.5}])

    def test_clarifying_a_paraphrase_recovers_the_verified_question(self):
        wording = "How warm was the branch where we hosted the symposium?"
        self.replies.append({"clarification": None, "matches": []})
        self.turn("discover", wording)
        self.replies.append(dict(base=None, joins=[], office_filters=[], clarification="Which city should I use?"))
        view = self.turn("answer_here", wording)
        self.assertTrue(view["needs_prior_question"])
        self.scope("external.weather_daily")
        self.fields([("weather_daily.temp_c", "avg")], [("weather_daily.city_code", "=", ["SHA"])])
        view = self.turn("followup", "Shanghai", prior_question=wording)
        self.assertEqual(self.requests[-2][1]["pending_question"], wording)
        self.assertEqual(view["evidence"]["rows"], [{"avg_temp_c": 14.5}])
        saved = conversation.saver.get_tuple({"configurable": {"thread_id": self.thread}})
        self.assertNotIn("symposium", str(saved.checkpoint))

    def test_client_state_cannot_choose_an_unrecommended_dashboard(self):
        self.replies.append({"clarification": None, "matches": [model_match("estate")]})
        original = self.turn("discover", "Show offices")
        error = self.events("select", state={"selected": "hiring", "stage": "answer"})[-1]
        self.assertEqual(error["type"], "RUN_ERROR")
        self.assertEqual(self.turn("restore"), original)

    def test_old_discovery_retry_cannot_return_new_dashboard_matches(self):
        old_run = str(uuid4())
        first = "How many people work in London?"
        self.replies.append(dict(matches=[model_match("workforce")], clarification=None))
        self.turn("discover", first, run=old_run)
        self.scope("workforce.employees", "employee_office", offices=["London"])
        self.metric("employees", ["London"])
        self.turn("answer_here", first)

        self.replies.append(dict(matches=[model_match("estate")], clarification=None))
        current = self.turn("discover", "How many desks are available?")
        with patch("sqlite3.connect", side_effect=AssertionError):
            error = self.events("discover", first, run=old_run)[-1]
            self.assertEqual((error["type"], error["code"]), ("RUN_ERROR", "question"))
            self.assertIn("superseded", error["message"])
            self.assertEqual(self.turn("restore"), current)

    def test_empty_planner_clarification_returns_a_readable_question(self):
        question = "How many people are based in London?"
        self.replies.append(dict(matches=[], clarification=None))
        self.turn("discover", question)
        self.scope("workforce.employees", "employee_office", offices=["London"])
        self.replies.append(dict(plan=None, fields=None, clarification=""))
        with patch("app.conversation.execute_plan", side_effect=AssertionError):
            state = self.turn("answer_here", question)
        self.assertEqual(state["stage"], "clarify")
        self.assertEqual(state["notice"], "Which measure or fields would you like to explore?")
        self.assertIsNone(state["evidence"])

    def test_unknown_subject_clarifies_without_touching_sources(self):
        self.replies.extend([{"clarification": None, "matches": []}, dict(base=None, joins=[], office_filters=[], clarification="Which measure should I use?")])
        with patch("app.catalogue.table_definition", side_effect=AssertionError), \
             patch("app.conversation.execute_plan", side_effect=AssertionError):
            self.turn("discover", "How many?")
            view = self.turn("answer_here")
        self.assertEqual(view["stage"], "clarify")
        self.assertEqual(view["notice"], "Which measure should I use?")
        self.assertIsNone(view["evidence"])

    def test_discovery_clarification_keeps_the_original_question_for_answer_here(self):
        first = "How are we doing?"
        prompt = "Which measure and office should I use?"
        self.replies.append({"matches": [], "clarification": prompt})
        with patch("sqlite3.connect", side_effect=AssertionError("Discovery touched a database")):
            view = self.turn("discover", first)
            self.assertEqual(view["discovery_clarification"], prompt)
            self.assertEqual(view["dashboards"], [])
            self.assertEqual(self.turn("restore")["discovery_clarification"], prompt)
        detail = "Active employees in London"
        self.scope("workforce.employees", "employee_office")
        self.metric("employees", ["London"])
        answer = self.turn("answer_here", first, clarification=detail)
        for _, payload in self.requests[-2:]:
            self.assertEqual(payload["question"], first)
            self.assertEqual(payload["clarified_detail"], detail)
        self.assertEqual(answer["evidence"]["rows"], [{"value": 2}])
        self.assertIn(detail, answer["history"])

    def test_unapproved_source_path_and_out_of_scope_field_clarify(self):
        self.replies.append({"clarification": None, "matches": [model_match("estate")]})
        self.turn("discover", "Show offices")
        self.scope("locations.offices", "office_weather")
        with patch("app.catalogue.table_definition", side_effect=AssertionError):
            self.assertEqual(self.turn("answer_here", "Show offices")["stage"], "clarify")
        self.scope("locations.offices")
        self.fields([("employees.role", "field")])
        with patch("app.field_queries.read_evidence", side_effect=AssertionError):
            self.assertEqual(self.turn("followup", "List their roles")["stage"], "clarify")

    def test_unrecognised_private_filter_never_enters_memory_or_sql(self):
        first = "List offices"
        self.replies.append({"clarification": None, "matches": [model_match("estate")]})
        self.turn("discover", first)
        self.scope("locations.offices")
        self.fields([("offices.office_name", "field")], [("offices.office_name", "=", ["person@example.org"])])
        with patch("app.field_queries.read_evidence", side_effect=AssertionError):
            view = self.turn("answer_here", first)
        self.assertEqual(view["stage"], "clarify")
        self.assertNotIn("person@", json.dumps(view))
        saved = conversation.saver.get_tuple({"configurable": {"thread_id": self.thread}})
        self.assertNotIn("person@", str(saved.checkpoint))

    def test_bounded_checkpoints_history_and_eviction(self):
        first = "How many active employees are in Dubai?"
        with patch.object(conversation, "MAX_THREADS", 2), patch.object(conversation, "threads", type(conversation.threads)()):
            self.replies.append({"clarification": None, "matches": [model_match("workforce")]})
            self.turn("discover", first)
            for index in range(9):
                self.scope("workforce.employees", "employee_office")
                self.metric("employees", ["Dubai" if index == 0 else "Manchester"])
                latest = self.turn("answer_here") if index == 0 else self.turn("followup", "What about Manchester?")
            self.assertEqual(len(latest["history"]), conversation.HISTORY_SIZE)
            saved = list(conversation.saver.list({"configurable": {"thread_id": self.thread}}))
            self.assertEqual(len(saved), 1)
            values = saved[0].checkpoint["channel_values"]
            self.assertEqual(len(values["analyses"]), conversation.HISTORY_SIZE)
            self.assertFalse({"sql", "rows", "schema", "evidence"} & values.keys())
            for _ in range(2):
                self.replies.append({"clarification": None, "matches": [model_match("workforce")]})
                self.turn("discover", first, thread=str(uuid4()))
            self.assertEqual(self.turn("restore")["stage"], "new")
            self.assertEqual(self.events("answer_here")[-1]["type"], "RUN_ERROR")

    def test_step_streams_over_http_before_the_provider_finishes(self):
        entered, release = threading.Event(), threading.Event()
        def delayed_match():
            entered.set()
            if not release.wait(8):
                raise AssertionError("No step was delivered while discovery was in progress")
            return {"clarification": None, "matches": [model_match("workforce")]}
        self.replies.append(delayed_match)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
            worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            worker.start()
            try:
                deadline = time.monotonic() + 5
                while not server.started and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(server.started)
                payload = dict(threadId=self.thread, runId=str(uuid4()), state={}, forwardedProps={"action":"discover"},
                               messages=[dict(id="q", role="user", content="Who is on our payroll?")])
                events = []
                with httpx.stream("POST", f"http://127.0.0.1:{port}/ag-ui", json=payload, timeout=10,
                                  trust_env=False) as response:
                    for line in response.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        event = json.loads(line[6:])
                        events.append(event)
                        if event.get("stepName") == "discover_dashboards" and event["type"] == "STEP_STARTED":
                            self.assertTrue(entered.wait(3))
                            release.set()
                self.assertTrue(release.is_set())
                self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                self.assertEqual([e["snapshot"]["discovery_mode"] for e in events if e["type"] == "STATE_SNAPSHOT"][-1], "semantic")
            finally:
                release.set()
                server.should_exit = True
                worker.join(5)
