import json
import os
import unittest
from unittest import mock

from mu_cli.core.types import Message, Role
from mu_cli.providers.gemini import GeminiProvider
from mu_cli.providers.openai import OpenAIProvider


class ProvidersTests(unittest.TestCase):
    def test_openai_provider_requires_api_key(self) -> None:
        with self.assertRaises(ValueError):
            OpenAIProvider(api_key=None)

    @mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-openai-key"}, clear=True)
    @mock.patch("mu_cli.providers.openai.request.urlopen")
    def test_openai_provider_generate(self, mock_urlopen: mock.Mock) -> None:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "hello from openai"}}]}
        ).encode("utf-8")

        provider = OpenAIProvider(model="gpt-test")
        reply = provider.generate([Message(role=Role.USER, content="ping")])

        self.assertEqual("hello from openai", reply.message.content)
        req = mock_urlopen.call_args.args[0]
        self.assertIn("api.openai.com", req.full_url)
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual("gpt-test", payload["model"])

    def test_gemini_provider_requires_api_key(self) -> None:
        with self.assertRaises(ValueError):
            GeminiProvider(api_key=None)

    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini-key"}, clear=True)
    @mock.patch("mu_cli.providers.gemini.request.urlopen")
    def test_gemini_provider_generate(self, mock_urlopen: mock.Mock) -> None:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "hello from gemini"}]}}]}
        ).encode("utf-8")

        provider = GeminiProvider(model="gemini-test")
        reply = provider.generate([
            Message(role=Role.SYSTEM, content="be concise"),
            Message(role=Role.USER, content="ping"),
        ])

        self.assertEqual("hello from gemini", reply.message.content)
        req = mock_urlopen.call_args.args[0]
        self.assertIn("generativelanguage.googleapis.com", req.full_url)
        self.assertIn("key=test-gemini-key", req.full_url)
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual("be concise", payload["system_instruction"]["parts"][0]["text"])


if __name__ == "__main__":
    unittest.main()
