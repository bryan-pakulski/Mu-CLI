import json
import os
import unittest
from unittest import mock

from mu_cli.core.types import Message, Role
from mu_cli.providers.gemini import GeminiProvider
from mu_cli.providers.ollama import OllamaProvider
from mu_cli.providers.openai import OpenAIProvider


class ProvidersTests(unittest.TestCase):
    def test_openai_provider_requires_api_key(self) -> None:
        with self.assertRaises(ValueError):
            OpenAIProvider(api_key=None)

    @mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-openai-key"}, clear=True)
    @mock.patch("mu_cli.providers.openai.request.urlopen")
    def test_openai_provider_generate_parses_tool_calls(self, mock_urlopen: mock.Mock) -> None:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
            }
        ).encode("utf-8")

        provider = OpenAIProvider(model="gpt-test")
        reply = provider.generate(
            [Message(role=Role.USER, content="ping")],
            tools=[{"name": "read_file", "description": "desc", "schema": {"type": "object"}}],
        )

        self.assertEqual("read_file", reply.tool_calls[0].name)
        self.assertEqual("a.py", reply.tool_calls[0].args["path"])
        self.assertEqual("call_1", reply.tool_calls[0].call_id)
        req = mock_urlopen.call_args.args[0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertIn("tools", payload)

    def test_gemini_provider_requires_api_key(self) -> None:
        with self.assertRaises(ValueError):
            GeminiProvider(api_key=None)


    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini-key"}, clear=True)
    @mock.patch("mu_cli.providers.gemini.request.urlopen")
    def test_gemini_provider_preview_alias_uses_supported_model(self, mock_urlopen: mock.Mock) -> None:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                "usageMetadata": {},
            }
        ).encode("utf-8")

        provider = GeminiProvider(model="gemini-3.1-pro-preview")
        provider.generate([Message(role=Role.USER, content="ping")])

        req = mock_urlopen.call_args.args[0]
        self.assertIn("/models/gemini-2.5-pro:generateContent", req.full_url)

    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-gemini-key"}, clear=True)
    @mock.patch("mu_cli.providers.gemini.request.urlopen")
    def test_gemini_provider_generate_parses_function_call(self, mock_urlopen: mock.Mock) -> None:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"functionCall": {"name": "read_file", "args": {"path": "a.py"}}}
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 5,
                    "totalTokenCount": 15,
                },
            }
        ).encode("utf-8")

        provider = GeminiProvider(model="gemini-test")
        reply = provider.generate(
            [Message(role=Role.USER, content="ping")],
            tools=[{"name": "read_file", "description": "desc", "schema": {"type": "object"}}],
        )

        self.assertEqual("read_file", reply.tool_calls[0].name)
        self.assertEqual("a.py", reply.tool_calls[0].args["path"])
        req = mock_urlopen.call_args.args[0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertIn("tools", payload)



    def test_ollama_provider_converts_assistant_tool_call_arguments_to_object(self) -> None:
        provider = OllamaProvider(model="llama3.2", host="http://localhost:11434")
        messages = [
            Message(
                role=Role.ASSISTANT,
                content="",
                metadata={
                    "tool_calls": [
                        {"id": "call_1", "name": "read_file", "arguments": '{"path":"a.py"}'},
                    ]
                },
            )
        ]

        converted = provider._convert_messages(messages)

        args = converted[0]["tool_calls"][0]["function"]["arguments"]
        self.assertIsInstance(args, dict)
        self.assertEqual("a.py", args["path"])

    def test_ollama_provider_tool_result_message_omits_tool_call_id(self) -> None:
        provider = OllamaProvider(model="llama3.2", host="http://localhost:11434")
        messages = [
            Message(
                role=Role.TOOL_RESULT,
                name="read_file",
                content="ok",
                metadata={"tool_call_id": "call_1"},
            )
        ]

        converted = provider._convert_messages(messages)

        self.assertEqual("tool", converted[0]["role"])
        self.assertNotIn("tool_call_id", converted[0])

    @mock.patch("mu_cli.providers.ollama.request.urlopen")
    def test_ollama_provider_stream_emits_thinking_output_chunks(self, mock_urlopen: mock.Mock) -> None:
        stream_lines = [
            json.dumps({"message": {"content": "Thinking "}, "done": False}).encode("utf-8"),
            json.dumps({"message": {"content": "more"}, "prompt_eval_count": 3, "eval_count": 2, "done": True}).encode("utf-8"),
        ]
        mock_urlopen.return_value.__enter__.return_value.__iter__.return_value = iter(stream_lines)

        seen: list[dict] = []
        provider = OllamaProvider(model="llama3.2", host="http://localhost:11434", stream_callback=seen.append)
        reply = provider.generate([Message(role=Role.USER, content="ping")], stream=True)

        self.assertEqual("Thinking more", reply.message.content)
        self.assertEqual(2, len(seen))
        self.assertTrue(all(item.get("kind") == "thinking_output" for item in seen))

    @mock.patch("mu_cli.providers.ollama.request.urlopen")
    def test_ollama_provider_generate_parses_tool_calls(self, mock_urlopen: mock.Mock) -> None:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {"name": "read_file", "arguments": {"path": "a.py"}},
                        }
                    ],
                },
                "prompt_eval_count": 11,
                "eval_count": 4,
            }
        ).encode("utf-8")

        provider = OllamaProvider(model="llama3.2", host="http://localhost:11434")
        reply = provider.generate(
            [Message(role=Role.USER, content="ping")],
            tools=[{"name": "read_file", "description": "desc", "schema": {"type": "object"}}],
        )

        self.assertEqual("read_file", reply.tool_calls[0].name)
        self.assertEqual("a.py", reply.tool_calls[0].args["path"])
        req = mock_urlopen.call_args.args[0]
        self.assertIn("/api/chat", req.full_url)


if __name__ == "__main__":
    unittest.main()
