import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from azure.core.exceptions import ClientAuthenticationError
from openai import OpenAIError

from app import llm_connection as connection


OPENAI_ENV = {"OPENAI_API_KEY": "synthetic-key", "LLM_TEMPERATURE": "0.7"}
AZURE_ENV = {
    "LLM_PROVIDER": "azure", "LLM_TEMPERATURE": "0.7",
    "AZURE_TENANT_ID": "synthetic-tenant", "AZURE_CLIENT_ID": "synthetic-client",
    "AZURE_CLIENT_SECRET": "synthetic-secret",
    "AZURE_OPENAI_TOKEN_SCOPE": "https://synthetic.invalid/.default",
    "AZURE_OPENAI_ENDPOINT": "https://synthetic.invalid",
    "AZURE_OPENAI_API_VERSION": "synthetic-version",
    "AZURE_OPENAI_DEPLOYMENT": "synthetic-deployment",
}


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = patch.object(connection, "ROOT", Path(directory.name))
        root.start()
        self.addCleanup(root.stop)
        environment = patch.dict(os.environ, OPENAI_ENV, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_openai_defaults_reuse_and_close(self):
        with patch.object(connection, "OpenAI") as constructor:
            client = constructor.return_value
            with connection.llm_connection() as (active, model):
                self.assertIs(active, client)
                self.assertEqual(model, "gpt-4.1-mini")
                client.chat.completions.create.assert_not_called()
                for _ in range(2):
                    connection.create_completion(active, model, "synthetic instructions", "synthetic data")
                client.close.assert_not_called()
            constructor.assert_called_once_with(api_key="synthetic-key", timeout=60, max_retries=1)
            self.assertEqual(client.chat.completions.create.call_count, 2)
            client.close.assert_called_once()

    def test_temperature_invalid_before_client_setup(self):
        for value in (None, "", " ", "invalid", "-0.1", "2.1", "nan", "inf", "-inf"):
            with self.subTest(value=value), patch.object(connection, "OpenAI") as constructor:
                if value is None:
                    os.environ.pop("LLM_TEMPERATURE", None)
                else:
                    os.environ["LLM_TEMPERATURE"] = value
                with self.assertRaises(connection.LLMConfigurationError):
                    with connection.llm_connection():
                        self.fail("Invalid temperature was accepted")
                constructor.assert_not_called()

    def test_temperature_boundaries_and_request_revalidation(self):
        client = Mock()
        for value in ("0", "2", "0.7"):
            os.environ["LLM_TEMPERATURE"] = value
            connection.create_completion(client, "model", "instructions", "data")
            self.assertEqual(client.chat.completions.create.call_args.kwargs["temperature"], float(value))
        client.reset_mock()
        os.environ["LLM_TEMPERATURE"] = "invalid"
        with self.assertRaises(connection.LLMConfigurationError):
            connection.create_completion(client, "model", "instructions", "data")
        client.chat.completions.create.assert_not_called()

    def test_provider_and_openai_settings(self):
        for provider in ("", "other", "OPENAI"):
            os.environ["LLM_PROVIDER"] = provider
            with self.assertRaises(connection.LLMConfigurationError):
                with connection.llm_connection():
                    self.fail()
        os.environ["LLM_PROVIDER"] = "openai"
        for name in ("OPENAI_API_KEY", "OPENAI_MODEL"):
            with patch.dict(os.environ, {name: "  "}):
                with self.assertRaises(connection.LLMConfigurationError):
                    with connection.llm_connection():
                        self.fail()

    def test_dotenv_loads_before_settings_and_process_wins(self):
        (connection.ROOT / ".env").write_text(
            'OPENAI_API_KEY=file-key\nOPENAI_MODEL="file-model"\nLLM_TEMPERATURE=1.2\n', encoding="utf-8"
        )
        with patch.object(connection, "OpenAI") as constructor:
            with connection.llm_connection() as (_, model):
                self.assertEqual(model, "file-model")
                self.assertEqual(connection.read_temperature(), 0.7)
            self.assertEqual(constructor.call_args.kwargs["api_key"], "synthetic-key")

    def test_azure_callback_deployment_and_cleanup(self):
        with (
            patch.dict(os.environ, AZURE_ENV, clear=True),
            patch.object(connection, "EnvironmentCredential") as credential,
            patch.object(connection, "get_bearer_token_provider") as provider,
            patch.object(connection, "AzureOpenAI") as constructor,
        ):
            with connection.llm_connection() as (client, model):
                self.assertEqual(model, "synthetic-deployment")
                provider.assert_called_once_with(credential.return_value, AZURE_ENV["AZURE_OPENAI_TOKEN_SCOPE"])
                provider.return_value.assert_not_called()
                credential.return_value.get_token.assert_not_called()
                constructor.assert_called_once_with(
                    azure_endpoint=AZURE_ENV["AZURE_OPENAI_ENDPOINT"],
                    api_version=AZURE_ENV["AZURE_OPENAI_API_VERSION"],
                    azure_ad_token_provider=provider.return_value, timeout=60, max_retries=1,
                )
                connection.create_completion(client, model, "instructions", {"synthetic": True})
                self.assertEqual(client.chat.completions.create.call_args.kwargs["model"], model)
            client.close.assert_called_once()
            credential.return_value.close.assert_called_once()

    def test_azure_requires_only_its_settings_before_allocation(self):
        for name in AZURE_ENV:
            if not name.startswith("AZURE_"):
                continue
            for value in (None, "", " "):
                settings = dict(AZURE_ENV)
                if value is None:
                    settings.pop(name)
                else:
                    settings[name] = value
                with (
                    self.subTest(name=name, value=value),
                    patch.dict(os.environ, settings, clear=True),
                    patch.object(connection, "EnvironmentCredential") as credential,
                ):
                    with self.assertRaisesRegex(connection.LLMConfigurationError, name):
                        with connection.llm_connection():
                            self.fail()
                    credential.assert_not_called()

    def test_cleanup_on_body_error_and_constructor_failure(self):
        with (
            patch.dict(os.environ, AZURE_ENV, clear=True),
            patch.object(connection, "EnvironmentCredential") as credential,
            patch.object(connection, "get_bearer_token_provider"),
            patch.object(connection, "AzureOpenAI") as constructor,
        ):
            with self.assertRaisesRegex(RuntimeError, "application error"):
                with connection.llm_connection():
                    raise RuntimeError("application error")
            constructor.return_value.close.assert_called_once()
            credential.return_value.close.assert_called_once()
            credential.reset_mock()
            constructor.side_effect = OpenAIError("sensitive provider detail")
            with self.assertRaises(connection.LLMConnectionError) as raised:
                with connection.llm_connection():
                    self.fail()
            self.assertNotIn("sensitive", str(raised.exception))
            credential.return_value.close.assert_called_once()

    def test_credential_closes_even_when_client_close_fails(self):
        with (
            patch.dict(os.environ, AZURE_ENV, clear=True),
            patch.object(connection, "EnvironmentCredential") as credential,
            patch.object(connection, "get_bearer_token_provider"),
            patch.object(connection, "AzureOpenAI") as constructor,
        ):
            constructor.return_value.close.side_effect = OpenAIError("sensitive")
            with self.assertRaises(connection.LLMConnectionError):
                with connection.llm_connection():
                    pass
            credential.return_value.close.assert_called_once()

    def test_auth_and_provider_errors_are_safe(self):
        for error in (ClientAuthenticationError("synthetic-secret"), OpenAIError("synthetic-secret")):
            client = Mock()
            client.chat.completions.create.side_effect = error
            with self.assertRaises(connection.LLMConnectionError) as raised:
                connection.create_completion(client, "model", "instructions", "data")
            self.assertNotIn("synthetic-secret", str(raised.exception))
            self.assertTrue(raised.exception.__suppress_context__)

    def test_shared_request_contract_and_raw_response(self):
        client = Mock()
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        for data in ({"text": "سلام"}, ["سلام"], "سلام"):
            response = connection.create_completion(client, "model", "instructions", data, json_schema=schema)
            self.assertIs(response, client.chat.completions.create.return_value)
            request = client.chat.completions.create.call_args.kwargs
            self.assertEqual(set(request), {"model", "messages", "temperature", "store", "response_format"})
            self.assertEqual(request["messages"][0], {"role": "system", "content": "instructions"})
            self.assertEqual(request["messages"][1]["role"], "user")
            self.assertIn("سلام", request["messages"][1]["content"])
            self.assertEqual(request["response_format"]["json_schema"], {"name": "result", "schema": schema, "strict": True})
            self.assertFalse(request["store"])
        connection.create_completion(client, "model", "instructions", "data")
        self.assertNotIn("response_format", client.chat.completions.create.call_args.kwargs)

    def test_static_azure_token_cannot_override_refresh_callback(self):
        with patch.dict(os.environ, {**AZURE_ENV, "AZURE_OPENAI_AD_TOKEN": "synthetic"}, clear=True):
            with self.assertRaisesRegex(connection.LLMConfigurationError, "AZURE_OPENAI_AD_TOKEN"):
                with connection.llm_connection():
                    self.fail()

    def test_real_sdk_serialization_and_azure_callback_without_network(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={
                "id": "synthetic", "object": "chat.completion", "created": 0,
                "model": "synthetic", "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "synthetic answer"},
                }],
            })

        for settings, constructor_name in ((OPENAI_ENV, "OpenAI"), (AZURE_ENV, "AzureOpenAI")):
            real_constructor = getattr(connection, constructor_name)
            transport = httpx.Client(transport=httpx.MockTransport(respond))

            def construct(**kwargs):
                return real_constructor(**kwargs, http_client=transport)

            with (
                self.subTest(provider=constructor_name),
                patch.dict(os.environ, settings, clear=True),
                patch.object(connection, constructor_name, side_effect=construct),
                patch.object(connection, "EnvironmentCredential"),
                patch.object(connection, "get_bearer_token_provider", return_value=Mock(return_value="synthetic-token")) as provider,
            ):
                count = len(requests)
                with connection.llm_connection() as (client, model):
                    self.assertEqual(len(requests), count)
                    for _ in range(2):
                        result = connection.create_completion(client, model, "instructions", {"text": "سلام"})
                        self.assertEqual(result.choices[0].message.content, "synthetic answer")
                    payload = json.loads(requests[-1].content)
                    self.assertEqual(payload["model"], model)
                    self.assertEqual(payload["temperature"], 0.7)
                    self.assertFalse(payload["store"])
                    if constructor_name == "AzureOpenAI":
                        self.assertEqual(provider.return_value.call_count, 2)
                        self.assertEqual(requests[-1].headers["authorization"], "Bearer synthetic-token")
                        self.assertIn("/deployments/synthetic-deployment/chat/completions", requests[-1].url.path)
                        self.assertEqual(requests[-1].url.params["api-version"], "synthetic-version")
                self.assertTrue(transport.is_closed)


if __name__ == "__main__":
    unittest.main()
