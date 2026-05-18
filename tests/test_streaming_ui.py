"""Tests for visible token-streaming in the REPL.

Pins:
  * `RichUI` exposes the streaming surface methods the renderer looks
    for (`stream_assistant_delta`, `stream_assistant_end`,
    `stream_thinking_delta`, `stream_tool_call`).
  * Token chunks are printed live (not buffered for a final panel).
  * After streaming, `render_message("assistant", ...)` is a no-op so we
    don't double-render.
  * `streaming_enabled=False` collapses to the legacy panel render.
  * Reasoning chunks are styled differently from assistant text.
  * The full pipeline — provider deltas → StreamRenderer →
    RichUI.stream_assistant_delta — actually fires end-to-end.
"""

from unittest.mock import MagicMock, patch

import pytest

from mu.ui.stream import build_default_renderer
from providers.base import (
    LLMProvider,
    Message,
    MessagePart,
    ProviderResponse,
    StreamEvent,
)


def _make_rich_ui_with_recording_console():
    """RichUI with its `console.print` swapped for a list-recorder, so we
    can assert exactly what hit stdout in what order."""
    from mu.ui.rich_ui import RichUI

    ui = RichUI()
    captured = []

    def record(*args, **kwargs):
        # Render the markup-as-string approximation: the bits that actually
        # get printed to the user. We deliberately don't try to reproduce
        # full Rich formatting; we just want a faithful order-of-calls log.
        text = " ".join(str(a) for a in args)
        captured.append(("print", text, dict(kwargs)))

    ui.console = MagicMock()
    ui.console.print = record
    return ui, captured


# ============================================================ RichUI surface


def test_richui_exposes_streaming_methods():
    from mu.ui.rich_ui import RichUI

    ui = RichUI()
    for method in (
        "stream_assistant_start",
        "stream_assistant_delta",
        "stream_assistant_end",
        "stream_thinking_delta",
        "stream_tool_call",
    ):
        assert hasattr(ui, method), f"RichUI missing {method!r}"
        assert callable(getattr(ui, method))


def test_streaming_delta_outside_live_falls_back_to_console_print():
    """When no GenerationLive is active (test fixture without show_status),
    deltas still reach the console via the fallback path so the text isn't
    lost. The header is owned by the Live and only renders inside it."""
    ui, captured = _make_rich_ui_with_recording_console()
    ui.stream_assistant_delta("Hello, ")
    ui.stream_assistant_delta("world!")
    print_lines = [t for (k, t, kw) in captured if k == "print"]
    assert "Hello, " in print_lines
    assert "world!" in print_lines


def test_streaming_delta_prints_tokens_with_end_empty_string():
    """Each delta uses `end=""` so tokens flow inline (no per-token newline)."""
    ui, captured = _make_rich_ui_with_recording_console()
    ui.stream_assistant_delta("abc")
    delta_calls = [(t, kw) for (k, t, kw) in captured if k == "print" and t == "abc"]
    assert delta_calls
    _, kw = delta_calls[0]
    assert kw.get("end") == ""
    assert kw.get("soft_wrap") is True


def test_thinking_delta_uses_dim_italic_style():
    ui, captured = _make_rich_ui_with_recording_console()
    ui.stream_thinking_delta("planning the fix")
    # The dim-italic style markup should be in the printed argument.
    matching = [t for (k, t, kw) in captured if k == "print" and "planning" in t]
    assert matching
    assert "dim italic" in matching[0]


def test_streaming_end_is_a_noop_outside_live():
    """`stream_assistant_end` no longer prints anything itself — the
    GenerationLive CM exit handles final-state rendering. Outside an
    active Live the call is just a no-op."""
    ui, captured = _make_rich_ui_with_recording_console()
    ui.stream_assistant_end()
    assert captured == []


# ============================================================ render_message dedup


def test_render_message_skips_panel_after_streaming():
    """`render_message("assistant", text)` after a successful stream is a
    no-op — the text is already on screen and we don't want it duplicated
    in a panel."""
    ui, captured = _make_rich_ui_with_recording_console()
    ui.stream_assistant_delta("Hello, world")
    captured.clear()  # ignore stream output; only assert what comes next

    ui.render_message("assistant", "Hello, world", model_name="gpt-4o")
    # Nothing should print — the panel render is suppressed.
    assert captured == []


def test_render_message_renders_panel_without_streaming():
    """No streaming → legacy panel render still works."""
    ui, captured = _make_rich_ui_with_recording_console()
    # Suppress render_response's recursive print path to keep the assert
    # focused on the header line.
    with patch("mu.ui.rich_ui.render_response"):
        ui.render_message("assistant", "hi", model_name="gpt-4o")
    print_lines = [t for (k, t, kw) in captured if k == "print"]
    assert any("Assistant" in line and "gpt-4o" in line for line in print_lines)


def test_render_message_user_panel_always_prints():
    """User messages are unaffected by streaming state."""
    ui, captured = _make_rich_ui_with_recording_console()
    ui.stream_assistant_delta("prior assistant turn")
    captured.clear()
    ui.render_message("user", "fresh user input")
    # A Panel object was printed (we can't easily introspect, but the call fired).
    assert len(captured) >= 1


def test_consecutive_iterations_each_suppress_their_own_panel():
    """Iteration 1 streams → render_message suppressed → iteration 2 streams.
    Critical: each iteration's render_message must be suppressed because
    its own text was already streamed (and the user saw it via the Live)."""
    ui, captured = _make_rich_ui_with_recording_console()

    # Iteration 1 (no Live active, but the fallback path still sets _streamed_any_text)
    ui.stream_assistant_delta("first response")
    ui.stream_assistant_end()
    captured.clear()
    ui.render_message("assistant", "first response", model_name="m")
    assert captured == []  # suppressed

    # In production each iteration runs inside its own _GenerationLive
    # context which resets _streamed_any_text on enter. Simulate that by
    # opening + closing a real Live around the second iteration.
    cm = ui.show_status("Generating it 2/5")
    try:
        cm.__enter__()
        ui.stream_assistant_delta("second response")
    finally:
        cm.__exit__(None, None, None)

    captured.clear()
    ui.render_message("assistant", "second response", model_name="m")
    assert captured == []  # also suppressed — flag survived the Live exit


# ============================================================ streaming_enabled switch


def test_streaming_disabled_skips_token_prints():
    ui, captured = _make_rich_ui_with_recording_console()
    ui.set_variables({"streaming_enabled": False})
    ui.stream_assistant_delta("should not appear")
    ui.stream_assistant_end()
    # Nothing printed because streaming is off.
    assert captured == []


def test_streaming_disabled_falls_back_to_panel_render():
    ui, captured = _make_rich_ui_with_recording_console()
    ui.set_variables({"streaming_enabled": False})
    ui.stream_assistant_delta("ignored")
    captured.clear()
    with patch("mu.ui.rich_ui.render_response"):
        ui.render_message("assistant", "ignored", model_name="m")
    print_lines = [t for (k, t, kw) in captured if k == "print"]
    # The legacy panel header fires.
    assert any("Assistant" in line for line in print_lines)


# ============================================================ end-to-end via renderer


class _ChunkedProvider(LLMProvider):
    """Streams the provided text in 4-char chunks via real StreamEvents."""

    def __init__(self, model_name, text):
        super().__init__(model_name)
        self.name = "chunked"
        self._text = text

    def get_available_models(self):
        return [self.model_name]

    def stream(self, messages, system_prompt=None, thinking=False, tools=None, cache_hint=None, reasoning_effort=None):
        chunk_size = 4
        for i in range(0, len(self._text), chunk_size):
            yield StreamEvent(kind="text_delta", text=self._text[i : i + chunk_size])
        yield StreamEvent(kind="usage", input_tokens=1, output_tokens=1, total_tokens=2)
        yield StreamEvent(kind="done")

    def generate(self, *a, **kw):
        return self.drain_stream(self.stream(*a, **kw))

    def upload_file(self, *a, **kw):
        return None


def test_end_to_end_provider_deltas_reach_richui():
    ui, captured = _make_rich_ui_with_recording_console()
    renderer = build_default_renderer(ui)
    provider = _ChunkedProvider("test-model", "Hello, this is a streamed response.")
    events = provider.stream(messages=[Message(role="user", parts=[MessagePart(type="text", text="hi")])])
    response = renderer.consume(provider, events)

    # The drained response carries the full text.
    assert response.text == "Hello, this is a streamed response."
    # And the console saw multiple delta prints (not one panel).
    delta_chunks = [
        t
        for (k, t, kw) in captured
        if k == "print" and kw.get("end") == "" and "Assistant" not in t
    ]
    # 4-char chunking on 36-char text → ~9 chunks.
    assert len(delta_chunks) >= 5
    # Joined chunks reconstruct the full text.
    assert "".join(delta_chunks) == "Hello, this is a streamed response."


def test_thinking_deltas_route_to_thinking_method_via_renderer():
    """A provider that emits `thinking_delta` events routes to
    `stream_thinking_delta`, NOT to `stream_assistant_delta`."""

    class _Reasoner(LLMProvider):
        name = "reasoner"
        def get_available_models(self): return ["r"]
        def stream(self, *a, **kw):
            yield StreamEvent(kind="thinking_delta", text="thinking about it")
            yield StreamEvent(kind="text_delta", text="answer")
            yield StreamEvent(kind="done")
        def generate(self, *a, **kw):
            return self.drain_stream(self.stream(*a, **kw))
        def upload_file(self, *a, **kw): return None

    ui, captured = _make_rich_ui_with_recording_console()
    renderer = build_default_renderer(ui)
    provider = _Reasoner("r")
    renderer.consume(provider, provider.stream(messages=[]))

    text_lines = [t for (k, t, kw) in captured if k == "print"]
    # thinking text appears with dim italic styling
    assert any("thinking about it" in line and "dim italic" in line for line in text_lines)
    # answer appears as plain stream
    assert "answer" in text_lines


# ============================================================ Ollama specifically


def test_ollama_stream_routes_text_to_stream_assistant_delta(monkeypatch):
    """End-to-end with the real OllamaProvider mocked at the urllib layer."""
    import io
    import json
    import urllib.request
    from providers.ollama import OllamaProvider

    # Two NDJSON chunks of streamed assistant text, then the done sentinel.
    body_lines = [
        json.dumps({"message": {"content": "Hel"}}),
        json.dumps({"message": {"content": "lo from "}}),
        json.dumps({"message": {"content": "Ollama!"}}),
        json.dumps({"message": {"content": ""}, "done": True, "prompt_eval_count": 1, "eval_count": 5}),
    ]

    def fake_urlopen(req, **kw):
        # First call is the preflight /api/tags
        if getattr(req, "data", None) is None:
            payload = json.dumps({"models": [{"name": "llama3"}]}).encode("utf-8")
            mock = MagicMock()
            mock.__enter__ = MagicMock(return_value=mock)
            mock.__exit__ = MagicMock(return_value=False)
            mock.read.return_value = payload
            return mock
        # /api/chat — return an iterable of bytes lines
        mock = MagicMock()
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        mock.__iter__ = MagicMock(
            return_value=iter([line.encode("utf-8") + b"\n" for line in body_lines])
        )
        return mock

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    ui, captured = _make_rich_ui_with_recording_console()
    renderer = build_default_renderer(ui)
    provider = OllamaProvider("llama3", host="http://fake:1234")
    response = renderer.consume(
        provider,
        provider.stream(messages=[Message(role="user", parts=[MessagePart(type="text", text="hi")])]),
    )

    # Full text reconstructed
    assert response.text == "Hello from Ollama!"
    # Three deltas hit the console (one per non-empty content chunk)
    delta_chunks = [
        t
        for (k, t, kw) in captured
        if k == "print" and kw.get("end") == "" and "Assistant" not in t
    ]
    assert "Hel" in delta_chunks
    assert "lo from " in delta_chunks
    assert "Ollama!" in delta_chunks


def test_ollama_think_tags_route_to_thinking_via_streaming(monkeypatch):
    """`<think>…</think>` in Ollama output becomes `thinking_delta`
    events and renders with the dim-italic style."""
    import json
    from providers.ollama import OllamaProvider

    body_lines = [
        json.dumps({"message": {"content": "Plain "}}),
        json.dumps({"message": {"content": "<think>internal "}}),
        json.dumps({"message": {"content": "plan</think>"}}),
        json.dumps({"message": {"content": "answer"}, "done": True, "prompt_eval_count": 1, "eval_count": 2}),
    ]

    def fake_urlopen(req, **kw):
        if getattr(req, "data", None) is None:
            mock = MagicMock()
            mock.__enter__ = MagicMock(return_value=mock)
            mock.__exit__ = MagicMock(return_value=False)
            mock.read.return_value = json.dumps({"models": [{"name": "qwen-think"}]}).encode("utf-8")
            return mock
        mock = MagicMock()
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        mock.__iter__ = MagicMock(
            return_value=iter([line.encode("utf-8") + b"\n" for line in body_lines])
        )
        return mock

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    ui, captured = _make_rich_ui_with_recording_console()
    renderer = build_default_renderer(ui)
    provider = OllamaProvider("qwen-think", host="http://fake:1234")
    response = renderer.consume(
        provider,
        provider.stream(messages=[Message(role="user", parts=[MessagePart(type="text", text="x")])]),
    )

    # Final response excludes the <think>…</think> contents.
    assert "<think>" not in response.text
    assert "</think>" not in response.text
    assert "Plain " in response.text
    assert "answer" in response.text
    # The dim-italic thinking style fired with the inside-of-tag text.
    text_lines = [t for (k, t, kw) in captured if k == "print"]
    assert any("dim italic" in line and "internal" in line for line in text_lines)
