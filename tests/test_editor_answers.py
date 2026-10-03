"""Investigation completion must produce an answer, not an empty tool turn."""

import json
import threading
from unittest.mock import MagicMock

import pytest

from mu.editor.analysis import answer_messages
from mu.editor.service import EditorService, make_provider
from providers.base import Message, MessagePart, StreamEvent
from providers.ollama import OllamaProvider
from utils.token_estimator import estimate_tokens


class Scripted:
    def __init__(self, rounds):
        self.rounds = iter(rounds)
        self.requests = []
        self.closed = 0

    def stream(self, messages, **kwargs):
        self.requests.append((list(messages), kwargs))
        try:
            yield from next(self.rounds)
        finally:
            self.closed += 1


def request(**overrides):
    return dict(
        path="main.py",
        text="def main():\n    return 1\n",
        row=1,
        col=4,
        instruction="How do I add a provider?",
        provider={"name": "fake", "model": "fake"},
        **overrides,
    )


def tool(path, identifier):
    return StreamEvent(
        "tool_call_complete",
        tool_name="read",
        tool_args={"path": path},
        tool_call_id=identifier,
    )


def test_ollama_sends_all_batched_results_with_their_names():
    provider = OllamaProvider("test")
    messages = [
        Message(
            "assistant",
            [
                MessagePart(
                    "tool_call",
                    tool_name="read",
                    tool_args={"path": "base.py"},
                    tool_call_id="r",
                ),
                MessagePart(
                    "tool_call",
                    tool_name="search",
                    tool_args={"query": "provider"},
                    tool_call_id="s",
                ),
            ],
        ),
        Message(
            "tool",
            [
                MessagePart(
                    "tool_result",
                    tool_name="read",
                    tool_call_id="r",
                    tool_result={"text": "ACTUAL_INTERFACE"},
                ),
                MessagePart(
                    "tool_result",
                    tool_name="search",
                    tool_call_id="s",
                    tool_result=[{"text": "FACTORY_LOCATION"}],
                ),
            ],
        ),
    ]
    wire = provider._prepare_chat_messages(messages, "system")
    results = [message for message in wire if message["role"] == "tool"]
    assert [message["tool_name"] for message in results] == ["read", "search"]
    assert json.loads(results[0]["content"]) == {"text": "ACTUAL_INTERFACE"}
    assert json.loads(results[1]["content"]) == [{"text": "FACTORY_LOCATION"}]
    assert [message["role"] for message in wire] == [
        "system",
        "assistant",
        "tool",
        "tool",
    ]


def test_editor_analysis_explicitly_disables_thinking_on_the_wire(monkeypatch):
    payloads = []

    def urlopen(req, **kwargs):
        payloads.append(json.loads(req.data))
        response = MagicMock()
        response.__enter__.return_value = response
        response.__iter__.return_value = iter(
            [b'{"message":{"content":"Answer"},"done":true}\n']
        )
        return response

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    for purpose in ("analyze", "explain"):
        provider = make_provider("ollama", "test", purpose=purpose)
        provider._preflight_done, provider._cached_models = True, ["test"]
        list(
            provider.stream(
                [Message("user", [MessagePart("text", text="Question")])],
                thinking=False,
            )
        )
    assert all(payload["think"] is False for payload in payloads)


def test_tool_request_after_final_round_recovers_from_all_evidence(tmp_path):
    (tmp_path / "base.py").write_text("class BaseProvider: pass\n")
    (tmp_path / "factory.py").write_text(
        "def create_provider(): return BaseProvider()\n"
    )
    provider = Scripted(
        [
            [tool("base.py", "r1")],
            [tool("factory.py", "r2")],
            [
                StreamEvent("text_delta", text="I will search again."),
                tool("missing.py", "r3"),
            ],
            [
                StreamEvent(
                    "text_delta",
                    text="Implement the interface in base.py and register it with create_provider().",
                )
            ],
        ]
    )
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    result = editor.run("analyze", request(action="explain"), threading.Event())
    assert result["text"].startswith("Implement the interface")
    assert len(provider.requests) == 4
    assert provider.requests[2][1]["tools"] is None
    recovery, options = provider.requests[3]
    assert options["tools"] is None
    assert all(part.type == "text" for message in recovery for part in message.parts)
    payload = json.loads(recovery[0].parts[0].text)
    assert {"base.py", "factory.py"} <= {
        source["path"] for source in payload["evidence"]
    }
    assert "missing.py" not in json.dumps(payload)
    assert provider.closed == 4
    assert editor.history["workspace"][-1].parts[0].text == result["text"]


def test_empty_initial_response_gets_one_answer_retry(tmp_path):
    provider = Scripted(
        [[StreamEvent("done")], [StreamEvent("text_delta", text="Here is the answer.")]]
    )
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    assert (
        editor.run("analyze", request(), threading.Event())["text"]
        == "Here is the answer."
    )
    assert len(provider.requests) == 2
    assert provider.requests[1][1]["tools"] is None


@pytest.mark.parametrize(
    "events",
    [
        [StreamEvent("done")],
        [StreamEvent("text_delta", text=" \n\n")],
        [StreamEvent("thinking_delta", text="Private reasoning, never an answer.")],
        [
            StreamEvent("text_delta", text="Looking that up."),
            tool("main.py", "another-tool"),
        ],
    ],
)
def test_unfinished_answer_retry_is_failure_not_saved_history(tmp_path, events):
    provider = Scripted([[StreamEvent("done")], events])
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    with pytest.raises(RuntimeError, match="no answer"):
        editor.run("analyze", request(), threading.Event())
    assert not editor.history
    assert len(provider.requests) == 2 and provider.closed == 2


def test_cancellation_stops_answer_recovery(tmp_path):
    cancel = threading.Event()

    class Cancels:
        def __init__(self):
            self.count = 0

        def stream(self, *args, **kwargs):
            self.count += 1
            if self.count == 2:
                cancel.set()
            yield StreamEvent("done")

    provider = Cancels()
    editor = EditorService(str(tmp_path), lambda *a, **k: provider)
    assert editor.run("analyze", request(), cancel) == {}
    assert not editor.history


@pytest.mark.parametrize("limit", [512, 6000])
def test_recovery_evidence_fits_budget_without_mutating_original(limit):
    snapshot = {
        "path": "main.py",
        "focus": {"path": "main.py", "line": 1, "text": "🙂" * 4000},
    }
    sources = [
        {
            "path": f"module{i}.py",
            "line": 1,
            "text": "def sample():\n    return " + str(i) + "\n" * 1000,
        }
        for i in range(30)
    ]
    history = [Message("assistant", [MessagePart("text", text="old answer " * 4000)])]
    messages = answer_messages(
        "🙂" * 4000, snapshot, sources, history, token_limit=limit
    )
    assert estimate_tokens(messages[0].parts[0].text) <= limit
    assert snapshot["focus"]["text"] == "🙂" * 4000
    assert len(json.loads(messages[0].parts[0].text)["evidence"]) <= 12
