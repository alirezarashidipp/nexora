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
from app.catalogue import DASHBOARDS, DEMO_DIR, find_dashboards, read_dashboards
from app.demo_queries import analyse, check_demo_data, new_plan, open_sources, read_evidence
from app.llm_connection import LLMConfigurationError
from app.prompts import load_prompt
from app.schema import DEMO_COLUMNS
from build_demo_data import build_databases


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
        return httpx.Response(200, json={"id": "test", "model": "offline-test", "created": 0,
            "object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(reply)}}]})

    def tearDown(self):
        self.assertEqual(self.replies, [], "Expected provider request did not run")

    def events(self, action, question="", thread=None, run=None, dashboard="", state=None, prior_question=""):
        payload = {"threadId": thread or self.thread, "runId": run or str(uuid4()),
                   "messages": [{"id": "q", "role": "user", "content": question}] if question else [],
                   "state": state or {}, "forwardedProps": {"action": action, "dashboard": dashboard,
                                                          "prior_question": prior_question}}
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

    def scope(self, table, *joins):
        self.replies.append(dict(base=table, joins=list(joins), clarification=None))

    def metric(self, key, cities, group="total", start=None):
        plan = new_plan(key)
        plan.cities, plan.group_by, plan.start_date = cities, group, start
        self.replies.append(dict(plan=plan.model_dump(), fields=None, clarification=None))

    def fields(self, outputs, filters=(), sort=(), limit=None):
        fields = dict(projections=[dict(column=c, operation=o) for c, o in outputs],
                      filters=[dict(column=c, operator=o, values=v) for c, o, v in filters],
                      sort=[dict(position=i, direction=d) for i, d in sort], distinct=False, limit=limit)
        self.replies.append(dict(plan=None, fields=fields, clarification=None))

    def test_discovery_reads_only_metadata_then_answer_and_compare(self):
        first = "How many active employees are in Dubai?"
        self.replies.append({"dashboard_ids": ["workforce", "hiring"]})
        with patch("app.catalogue.table_definition", side_effect=AssertionError), \
             patch("app.demo_queries.read_evidence", side_effect=AssertionError):
            discovery = self.turn("discover", first)
            self.assertEqual(discovery["discovery_mode"], "semantic")
            self.assertEqual([d["dashboard_id"] for d in discovery["dashboards"]], ["workforce", "hiring"])
            self.assertEqual(self.turn("select", state={"selected": "workforce"})["selected"], "workforce")
        sent = self.requests[0][1]
        self.assertEqual(set(sent), {"question", "catalogue"})
        self.assertEqual(len(sent["catalogue"]), 10)
        for entry in sent["catalogue"]:
            self.assertEqual(set(entry), {"dashboard_id", "title", "description", "keywords", "kpi_tags"})
        self.scope("workforce.employees", "employee_office")
        self.metric("employees", ["Dubai"])
        result = self.turn("answer_here", "Other question", state={"selected": "hiring", "plan": {}})
        self.assertEqual(self.requests[-1][1]["question"], first)
        self.assertEqual(self.requests[-1][1]["selected_dashboard"], "workforce")
        self.assertEqual(result["evidence"]["rows"], [{"value": 2}])
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

    def test_paraphrases_can_match_without_any_keyword_hit(self):
        catalog = read_dashboards()
        for question, match in (("Who is on our payroll?", "workforce"),
                                ("Where do we keep burning through power?", "cost")):
            with self.subTest(question=question):
                self.assertEqual(find_dashboards(question, catalog), [])
                self.replies.append({"dashboard_ids": [match]})
                view = self.turn("discover", question, thread=str(uuid4()))
                self.assertEqual(view["discovery_mode"], "semantic")
                self.assertEqual([d["dashboard_id"] for d in view["dashboards"]], [match])
                self.assertEqual(self.requests[-1][0], load_prompt("dashboard_discovery"))

    def test_discovery_fallback_and_valid_semantic_no_match(self):
        question = "What was the energy cost in Manchester?"
        expected = find_dashboards(question, read_dashboards())
        for reply in ({"dashboard_ids": ["invented"]}, {"invalid": True}):
            self.replies.append(reply)
            view = self.turn("discover", question, thread=str(uuid4()))
            self.assertEqual(view["discovery_mode"], "keyword_fallback")
            self.assertEqual([d["dashboard_id"] for d in view["dashboards"]], expected)
        with patch("app.conversation_plan.llm_connection", side_effect=LLMConfigurationError("Offline")):
            view = self.turn("discover", question)
            self.assertEqual(view["discovery_mode"], "keyword_fallback")
            error = self.events("answer_here")[-1]
            self.assertEqual((error["type"], error["code"]), ("RUN_ERROR", "provider"))
            self.assertEqual(self.turn("restore"), view)
        self.replies.append({"dashboard_ids": []})
        view = self.turn("discover", question, thread=str(uuid4()))
        self.assertEqual((view["discovery_mode"], view["dashboards"]), ("semantic", []))

    def test_every_suggested_question_uses_the_real_planner(self):
        cases = (("workforce", "employees", "Dubai"), ("cost", "energy_cost", "Manchester"),
                 ("weather", "rain_attendance", "Mumbai"), ("portfolio", "staffing_gap", "Shanghai"))
        examples = self.client.get("/conversation/examples").json()
        self.assertEqual(len(examples), len(cases))
        for question, (report, metric, city) in zip(examples, cases):
            thread = str(uuid4())
            self.replies.append({"dashboard_ids": [report]})
            self.scope(DASHBOARDS[report].table, *DASHBOARDS[report].joins)
            self.metric(metric, [city])
            self.turn("discover", question, thread=thread)
            view = self.turn("answer_here", question, thread=thread)
            self.assertTrue(view["evidence"]["rows"])
            last = self.requests[-3:]
            self.assertEqual([prompt for prompt, _ in last], [load_prompt(n) for n in
                             ("dashboard_discovery", "conversation_scope", "conversation_plan")])
            self.assertTrue(all(body["question"] == question for _, body in last))

    def test_unseen_ranked_question_outside_dashboard_metrics(self):
        question = "Rank the three biggest workplaces by indoor area."
        self.replies.append({"dashboard_ids": []})
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
        self.replies.append({"dashboard_ids": ["occupancy"]})
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
        self.replies.append({"dashboard_ids": []})
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
        self.replies.append({"dashboard_ids": ["cost"]})
        state = self.turn("discover", question)
        self.assertTrue(state["needs_original"])
        self.assertNotIn("person@", json.dumps(state))
        self.assertEqual(self.events("answer_here", "Another question")[-1]["type"], "RUN_ERROR")
        self.assertEqual(self.turn("restore"), state)
        self.turn("select", state={"selected": "cost"})
        self.replies.append(dict(base=None, joins=[], clarification="Which city should I use?"))
        self.turn("answer_here", question)
        self.assertEqual(self.requests[-1][1]["question"], question)
        saved = list(conversation.saver.list({"configurable": {"thread_id": self.thread}}))
        self.assertEqual(len(saved), 1)
        for word in ("person@", "SELECT ", "dashboard_url", "possible_values"):
            self.assertNotIn(word, str(saved[0].checkpoint))

    def test_clarifying_a_paraphrase_recovers_the_verified_question(self):
        wording = "How warm was the branch where we hosted the symposium?"
        self.replies.append({"dashboard_ids": []})
        self.turn("discover", wording)
        self.replies.append(dict(base=None, joins=[], clarification="Which city should I use?"))
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
        self.replies.append({"dashboard_ids": ["estate"]})
        original = self.turn("discover", "Show offices")
        error = self.events("select", state={"selected": "hiring", "stage": "answer"})[-1]
        self.assertEqual(error["type"], "RUN_ERROR")
        self.assertEqual(self.turn("restore"), original)

    def test_unknown_subject_clarifies_without_touching_sources(self):
        self.replies.extend([{"dashboard_ids": []}, dict(base=None, joins=[], clarification="Which measure should I use?")])
        with patch("app.catalogue.table_definition", side_effect=AssertionError), \
             patch("app.conversation.execute_plan", side_effect=AssertionError):
            self.turn("discover", "How many?")
            view = self.turn("answer_here")
        self.assertEqual(view["stage"], "clarify")
        self.assertEqual(view["notice"], "Which measure should I use?")
        self.assertIsNone(view["evidence"])

    def test_unapproved_source_path_and_out_of_scope_field_clarify(self):
        self.replies.append({"dashboard_ids": ["estate"]})
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
        self.replies.append({"dashboard_ids": ["estate"]})
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
            self.replies.append({"dashboard_ids": ["workforce"]})
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
                self.replies.append({"dashboard_ids": ["workforce"]})
                self.turn("discover", first, thread=str(uuid4()))
            self.assertEqual(self.turn("restore")["stage"], "new")
            self.assertEqual(self.events("answer_here")[-1]["type"], "RUN_ERROR")

    def test_step_streams_over_http_before_the_provider_finishes(self):
        entered, release = threading.Event(), threading.Event()
        def delayed_match():
            entered.set()
            if not release.wait(8):
                raise AssertionError("No step was delivered while discovery was in progress")
            return {"dashboard_ids": ["workforce"]}
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
