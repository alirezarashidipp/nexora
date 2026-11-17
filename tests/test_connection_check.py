import io
import unittest
from contextlib import nullcontext, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import connection_check
from app.llm_connection import LLMConfigurationError, LLMConnectionError


class ConnectionCheckTests(unittest.TestCase):
    def test_synthetic_success_is_explicit_and_does_not_print_response(self):
        client = Mock()
        response = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(
                content='{"marker":"SYNTHETIC_CONNECTION_CHECK"}', refusal=None,
            ),
        )])
        output = io.StringIO()
        with (
            patch.object(connection_check, "llm_connection", return_value=nullcontext((client, "synthetic-model"))),
            patch.object(connection_check, "create_completion", return_value=response) as create,
            redirect_stdout(output),
        ):
            self.assertEqual(connection_check.main(), 0)
        self.assertIn("LIVE", output.getvalue())
        self.assertIn("succeeded", output.getvalue())
        self.assertNotIn("SYNTHETIC_CONNECTION_CHECK", output.getvalue())
        self.assertEqual(create.call_args.args[3], {"marker": "SYNTHETIC_CONNECTION_CHECK"})
        self.assertFalse(create.call_args.kwargs["json_schema"]["additionalProperties"])

    def test_configuration_failure_does_not_send_request(self):
        output = io.StringIO()
        with (
            patch.object(connection_check, "llm_connection", side_effect=LLMConfigurationError("OPENAI_API_KEY is required.")),
            patch.object(connection_check, "create_completion") as create,
            redirect_stdout(output),
        ):
            self.assertEqual(connection_check.main(), 1)
        create.assert_not_called()
        self.assertIn("no successful live check", output.getvalue())

    def test_failure_is_nonzero_and_hides_provider_details(self):
        output = io.StringIO()
        with (
            patch.object(connection_check, "llm_connection", side_effect=LLMConnectionError("synthetic secret")),
            redirect_stdout(output),
        ):
            self.assertEqual(connection_check.main(), 1)
        self.assertNotIn("synthetic secret", output.getvalue())

    def test_wrong_synthetic_marker_fails(self):
        response = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(
                content='{"marker":"unexpected sensitive content"}', refusal=None,
            ),
        )])
        output = io.StringIO()
        with (
            patch.object(connection_check, "llm_connection", return_value=nullcontext((Mock(), "model"))),
            patch.object(connection_check, "create_completion", return_value=response),
            redirect_stdout(output),
        ):
            self.assertEqual(connection_check.main(), 1)
        self.assertNotIn("unexpected sensitive content", output.getvalue())
