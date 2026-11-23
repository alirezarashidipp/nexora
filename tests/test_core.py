import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = {}
for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("=")
    if separator and key and not key.lstrip().startswith("#"):
        DEFAULT_ENV[key.strip()] = value.strip()

SOURCE_DATABASE = Path(DEFAULT_ENV["DATABASE_PATH"])
if not SOURCE_DATABASE.is_absolute():
    SOURCE_DATABASE = ROOT / SOURCE_DATABASE

TEST_DIRECTORY = tempfile.TemporaryDirectory()
TEST_DATABASE = Path(TEST_DIRECTORY.name) / SOURCE_DATABASE.name
shutil.copyfile(SOURCE_DATABASE, TEST_DATABASE)
os.environ.update(
    {
        "OPENAI_API_KEY": "test-key",
        "OPENAI_MODEL": "test-model",
        "LLM_PROVIDER": "openai",
        "LLM_TEMPERATURE": "0.7",
        "DATABASE_PATH": str(TEST_DATABASE),
    }
)

from app import api, config, database, workflow
from app import llm_connection as connection
from app.config import STATIC_DIR
from app.schema import COLUMN_GUIDE, DEMO_COLUMNS, TABLE_NAME


class ConfigTests(unittest.TestCase):
    def test_env_fallback_preserves_process_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("TEST_SETTING=file\nTEST_OTHER=loaded\n")
            with (
                patch.object(connection, "ROOT", root),
                patch.dict(os.environ, {"TEST_SETTING": "process"}, clear=True),
            ):
                config.load_local_env()
                self.assertEqual(os.environ["TEST_SETTING"], "process")
                self.assertEqual(os.environ["TEST_OTHER"], "loaded")

    def test_only_root_env_is_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("TEST_SETTING=fallback\n")
            (root / ".env.local").write_text("TEST_SETTING=local\n")
            with (
                patch.object(connection, "ROOT", root),
                patch.dict(os.environ, {}, clear=True),
            ):
                config.load_local_env()
                self.assertEqual(os.environ["TEST_SETTING"], "fallback")


def tearDownModule():
    database.table_schema.cache_clear()
    database.describe_table.cache_clear()
    TEST_DIRECTORY.cleanup()


def _row_values(row):
    return tuple(
        sorted((type(value).__name__, repr(value)) for value in row.values())
    )


def rows_equal(actual, expected, mode):
    if mode == "scalar":
        return (
            len(actual) == len(expected) == 1
            and len(actual[0]) == len(expected[0]) == 1
            and next(iter(actual[0].values()))
            == next(iter(expected[0].values()))
        )

    if mode == "ordered":
        return list(map(_row_values, actual)) == list(map(_row_values, expected))

    if mode == "unordered":
        return sorted(map(_row_values, actual)) == sorted(map(_row_values, expected))

    if mode == "grouped":
        # ponytail: grouped results assume one text label and one numeric value;
        # add explicit label/value keys if numeric group labels become a real case.
        def grouped(rows):
            result = {}
            for row in rows:
                values = list(row.values())
                numbers = [
                    value
                    for value in values
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                ]
                labels = [value for value in values if value not in numbers]
                if len(values) != 2 or len(numbers) != 1 or len(labels) != 1:
                    return None
                try:
                    if labels[0] in result:
                        return None
                    result[labels[0]] = numbers[0]
                except TypeError:
                    return None
            return result

        actual_groups = grouped(actual)
        return actual_groups is not None and actual_groups == grouped(expected)

    raise ValueError(f"Unknown comparison mode: {mode}")


class CoreDatabaseTests(unittest.TestCase):
    def test_existing_database_matches_column_guide(self):
        rows = database.execute_sql(
            f'SELECT COUNT(*) AS total FROM "{TABLE_NAME}"'
        )
        self.assertGreaterEqual(rows[0]["total"], 0)
        self.assertEqual(
            {column["name"] for column in database.table_schema()},
            set(COLUMN_GUIDE),
        )

    def test_missing_database_is_not_created(self):
        missing_path = Path(TEST_DIRECTORY.name) / "missing.db"
        database.table_schema.cache_clear()
        self.addCleanup(database.table_schema.cache_clear)
        with patch.object(database, "DB_PATH", missing_path):
            with self.assertRaisesRegex(ValueError, "does not exist"):
                database.table_schema()
            self.assertFalse(missing_path.exists())

    def test_sql_validation_rejects_unsafe_queries(self):
        unsafe_queries = (
            f'DELETE FROM "{TABLE_NAME}"',
            f'SELECT * FROM "{TABLE_NAME}_other"',
            f'SELECT * FROM "{TABLE_NAME}"; DELETE FROM "{TABLE_NAME}"',
            f'SELECT * FROM "{TABLE_NAME}" -- ignore safeguards',
        )
        for query in unsafe_queries:
            with self.subTest(query=query), self.assertRaises(ValueError):
                database.validate_sql(query)

        valid = f'SELECT COUNT(*) FROM "{TABLE_NAME}"'
        self.assertEqual(database.validate_sql(valid), valid)

    def test_query_must_really_read_the_configured_table(self):
        with self.assertRaisesRegex(ValueError, "must read"):
            database.execute_sql(f"SELECT 'FROM {TABLE_NAME} ' AS fake")

    def test_database_file_is_readonly(self):
        with closing(database._connect_readonly()) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(f'DELETE FROM "{TABLE_NAME}"')

    def test_sqlite_authorizer_denies_other_tables(self):
        query = (
            f'SELECT sqlite_master.name FROM "{TABLE_NAME}" '
            "CROSS JOIN sqlite_master"
        )
        with self.assertRaises(sqlite3.DatabaseError):
            database.execute_sql(query)

    def test_query_returns_all_matching_rows(self):
        total = database.execute_sql(
            f'SELECT COUNT(*) AS total FROM "{TABLE_NAME}"'
        )[0]["total"]
        rows = database.execute_sql(
            f'SELECT * FROM "{TABLE_NAME}" UNION ALL SELECT * FROM "{TABLE_NAME}"'
        )
        self.assertEqual(len(rows), total * 2)


class ContractTests(unittest.TestCase):
    def test_catalogue_lists_public_metadata_without_reading_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "urls.json"
            settings.write_text(json.dumps({
                "workforce": "https://reports.company.com/workforce",
                "estate": None,
            }), encoding="utf-8")
            with (
                patch.dict(os.environ, {"NEXORA_DASHBOARD_URLS_FILE": str(settings)}),
                patch.object(sqlite3, "connect", side_effect=AssertionError("Unexpected database read")),
            ):
                response = TestClient(api.app).get("/catalogue")

        self.assertEqual(response.status_code, 200)
        result = response.json()
        dashboards = {item["dashboard_id"]: item for item in result["dashboards"]}
        self.assertEqual(set(dashboards), {"workforce", "estate", "portfolio", "hiring", "occupancy", "cost"})
        for item in dashboards.values():
            self.assertEqual(set(item), {"dashboard_id", "title", "description", "dashboard_url", "url_status"})
            self.assertTrue(item["title"] and item["description"])
        self.assertEqual(dashboards["workforce"]["dashboard_url"], "https://reports.company.com/workforce")
        self.assertEqual(dashboards["workforce"]["url_status"], "configured")
        self.assertIsNone(dashboards["estate"]["dashboard_url"])
        self.assertEqual(dashboards["portfolio"]["url_status"], "sample")

        tables = {}
        for source in result["databases"]:
            self.assertTrue(source["description"])
            self.assertTrue(source["name"].endswith(".db"))
            for table in source["tables"]:
                self.assertEqual(set(table), {"name", "columns"})
                key = source["name"].removesuffix(".db") + "." + table["name"]
                tables[key] = table["columns"]
        expected = {name: list(columns) for name, columns in DEMO_COLUMNS.items()}
        expected["main_datawarehouse." + TABLE_NAME] = list(COLUMN_GUIDE)
        self.assertEqual(tables, expected)

    def test_invalid_dashboard_catalogue_returns_safe_error(self):
        with patch.object(api, "read_dashboards", side_effect=ValueError("private details")):
            response = TestClient(api.app).get("/catalogue")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "The dashboard catalogue is unavailable.")

    def test_provider_errors_are_redacted_and_configuration_errors_are_safe(self):
        from fastapi import HTTPException
        from openai import OpenAIError

        cases = (
            (connection.LLMConfigurationError("LLM_TEMPERATURE is required."), 500),
            (connection.LLMConnectionError("synthetic secret"), 502),
            (OpenAIError("synthetic secret"), 502),
        )
        for error, status in cases:
            with patch.object(api.question_graph, "invoke", side_effect=error):
                with self.assertRaises(HTTPException) as raised:
                    api.ask(api.QuestionRequest(question="Synthetic question"))
            self.assertEqual(raised.exception.status_code, status)
            self.assertNotIn("synthetic secret", raised.exception.detail)

    def test_schema_endpoint_returns_runtime_columns(self):
        columns = [
            {
                "name": "RUNTIME_COLUMN",
                "type": "TEXT",
                "description": "Runtime description.",
                "possible_values": "Any.",
            }
        ]
        with (
            patch.object(api, "table_schema", return_value=columns),
            patch.object(api, "EXAMPLE_QUESTIONS", ("Runtime example?",)),
        ):
            payload = json.loads(json.dumps(api.schema()))

        self.assertEqual(
            payload,
            {
                "table": TABLE_NAME,
                "columns": columns,
                "examples": ["Runtime example?"],
            },
        )

    def test_ask_endpoint_keeps_one_json_contract_for_every_status(self):
        cases = (
            (
                {
                    "status": "valid",
                    "normalized_question": "Normalized question",
                    "answer": "One row.",
                    "sql": "SELECT 1",
                    "rows": [{"total": 1}],
                },
                "Normalized question",
                "SELECT 1",
                [{"total": 1}],
            ),
            ({"status": "incomplete", "answer": "Add details."}, "Show data", None, []),
            ({"status": "invalid", "answer": "Out of scope."}, "Show data", None, []),
        )

        for graph_result, normalized, sql, rows in cases:
            with self.subTest(status=graph_result["status"]), patch.object(
                api.question_graph, "invoke", return_value=graph_result
            ):
                payload = json.loads(
                    json.dumps(api.ask(api.QuestionRequest(question="Show data")))
                )

                self.assertEqual(
                    set(payload),
                    {
                        "question",
                        "normalized_question",
                        "status",
                        "answer",
                        "sql",
                        "rows",
                    },
                )
                self.assertEqual(payload["normalized_question"], normalized)
                self.assertEqual(payload["sql"], sql)
                self.assertEqual(payload["rows"], rows)

    def test_workspace_offers_sample_questions_and_removes_the_directory(self):
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn("/conversation/examples", script)
        self.assertIn('id="sample-questions"', html)
        self.assertNotIn("Employee directory", html)
        self.assertNotIn('href="#data-sources"', html)
        self.assertNotIn('fetch("/schema")', script)


class WorkflowTests(unittest.TestCase):
    def test_invalid_or_refused_output_never_executes_sql(self):
        cases = (
            ("not JSON synthetic secret", None, "stop"),
            ('{"sql": 123}', None, "stop"),
            ('{"sql": "SELECT 1", "extra": true}', None, "stop"),
            (None, "synthetic refusal", "stop"),
            ('{"sql": "SELECT 1"}', None, "length"),
        )
        for content, refusal, finish_reason in cases:
            response = SimpleNamespace(choices=[SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, refusal=refusal),
            )])
            with (
                patch.object(workflow, "llm_connection", return_value=nullcontext((Mock(), "model"))),
                patch.object(workflow, "create_completion", return_value=response),
                patch.object(workflow, "execute_sql") as execute,
            ):
                with self.assertRaises(ValueError) as raised:
                    workflow.query_database({"schema": "synthetic", "normalized_question": "synthetic"})
                self.assertNotIn("synthetic secret", str(raised.exception))
                execute.assert_not_called()

    @staticmethod
    def _client(*parsed, answer="Answer."):
        completions = Mock()
        values = [value.model_dump_json() if value is not None else None for value in parsed]
        values.append(answer)
        completions.create.side_effect = [
            SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="stop", message=SimpleNamespace(content=value, refusal=None)
            )]) for value in values
        ]
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    def test_valid_question_routes_through_sql_and_answer(self):
        sql = f'SELECT COUNT(*) AS total FROM "{TABLE_NAME}"'
        client = self._client(
            workflow.QuestionReview(
                status="valid",
                normalized_question="Normalized question",
                message="",
            ),
            workflow.SqlQuery(sql=f" {sql} "),
            answer="One row.",
        )

        with (
            patch.object(workflow, "describe_table", return_value="schema"),
            patch.object(workflow, "llm_connection", side_effect=lambda: nullcontext((client, "test-model"))),
            patch.object(
                workflow, "execute_sql", return_value=[{"total": 1}]
            ) as execute_sql,
        ):
            result = workflow.question_graph.invoke({"question": "Question"})

        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["sql"], sql)
        self.assertEqual(result["rows"], [{"total": 1}])
        self.assertEqual(result["answer"], "One row.")
        self.assertEqual(client.chat.completions.create.call_count, 3)
        execute_sql.assert_called_once_with(sql)

    def test_non_valid_questions_never_execute_sql(self):
        for status in ("incomplete", "invalid"):
            with self.subTest(status=status):
                client = self._client(
                    workflow.QuestionReview(
                        status=status,
                        normalized_question="Normalized question",
                        message="Stop.",
                    )
                )
                with (
                    patch.object(workflow, "describe_table", return_value="schema"),
                    patch.object(
                        workflow, "llm_connection", side_effect=lambda: nullcontext((client, "test-model"))
                    ),
                    patch.object(workflow, "execute_sql") as execute_sql,
                ):
                    result = workflow.question_graph.invoke(
                        {"question": "Question"}
                    )

                self.assertEqual(result["status"], status)
                self.assertEqual(result["answer"], "Stop.")
                self.assertEqual(client.chat.completions.create.call_count, 1)
                execute_sql.assert_not_called()

    def test_generated_sql_executes_through_the_real_guardrails(self):
        sql = f'SELECT COUNT(*) AS total FROM "{TABLE_NAME}"'
        client = self._client(workflow.SqlQuery(sql=sql))
        with patch.object(workflow, "llm_connection", side_effect=lambda: nullcontext((client, "test-model"))):
            result = workflow.query_database(
                {
                    "schema": database.describe_table(),
                    "normalized_question": "How many rows?",
                }
            )

        expected = database.execute_sql(sql)
        self.assertTrue(rows_equal(result["rows"], expected, "scalar"))

    def test_answer_model_receives_the_question_sql_and_rows(self):
        client = self._client(answer="Seven.")
        state = {
            "normalized_question": "How many rows?",
            "sql": f'SELECT COUNT(*) AS total FROM "{TABLE_NAME}"',
            "rows": [{"total": 7}],
        }
        with patch.object(workflow, "llm_connection", side_effect=lambda: nullcontext((client, "test-model"))):
            result = workflow.generate_answer(state)

        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(json.loads(request["messages"][1]["content"]), {
            "question": state["normalized_question"],
            "sql": state["sql"],
            "rows": state["rows"],
        })
        self.assertIn("using only the supplied SQLite result", request["messages"][0]["content"])
        self.assertFalse(request["store"])
        self.assertEqual(result["answer"], "Seven.")


class ResultComparisonTests(unittest.TestCase):
    def test_supported_result_comparison_modes(self):
        self.assertTrue(rows_equal([{"TOTAL": 2}], [{"count": 2}], "scalar"))
        self.assertTrue(
            rows_equal(
                [{"name": "B", "count": 2}, {"name": "A", "count": 1}],
                [{"label": "A", "value": 1}, {"label": "B", "value": 2}],
                "grouped",
            )
        )
        actual = [{"name": "B"}, {"name": "A"}]
        expected = [{"label": "A"}, {"label": "B"}]
        self.assertTrue(rows_equal(actual, expected, "unordered"))
        self.assertFalse(rows_equal(actual, expected, "ordered"))
        self.assertTrue(rows_equal(list(reversed(actual)), expected, "ordered"))

        with self.assertRaises(ValueError):
            rows_equal([], [], "unknown")


if __name__ == "__main__":
    unittest.main()
