"""Tests for the unified `_GenerationLive` and toggle-banner feedback.

The bug-fix that drives these tests: previously `stream_assistant_delta`
called `console.print(text, end="")` *during* an active Rich `Status`
(which is itself a `Live`), causing tokens to scatter and the spinner
to drift around. The fix routes all streaming sources into one Live so
the status footer stays anchored at the bottom and tokens accumulate
cleanly above.

Toggle-feedback fix: `/thinking`, `/yolo`, `/agentic` previously returned
a `CommandResult.message` that never got printed. They now call
`session.ui.show_info` directly with a styled banner.
"""

from unittest.mock import MagicMock

import pytest

from mu.commands.stats import (
    agentic_cmd,
    stats_cmd,
    thinking_cmd,
    yolo_cmd,
)


# ============================================================ toggle banners


class _RecordingUI:
    def __init__(self):
        self.info_calls = []
        self.error_calls = []
    def show_info(self, m):
        self.info_calls.append(str(m))
    def show_error(self, m):
        self.error_calls.append(str(m))


class _SessionStub:
    def __init__(self):
        self.ui = _RecordingUI()
        self.thinking = False
        self.agentic = True
        self.variables = {"yolo": False}
        self.session_manager = MagicMock()
        self.folder_context = MagicMock()


def test_thinking_command_emits_visible_banner():
    session = _SessionStub()
    result = thinking_cmd(session, "", allow_prompt=True)
    assert result.ok is True
    assert session.thinking is True
    # A banner reached the UI — not just a CommandResult.message.
    assert session.ui.info_calls, "expected a show_info banner for /thinking"
    assert "Thinking mode" in session.ui.info_calls[0]
    assert "ON" in session.ui.info_calls[0]


def test_thinking_command_toggles_off_with_banner():
    session = _SessionStub()
    session.thinking = True
    result = thinking_cmd(session, "", allow_prompt=True)
    assert session.thinking is False
    assert any("OFF" in m for m in session.ui.info_calls)


def test_yolo_command_emits_visible_banner():
    session = _SessionStub()
    result = yolo_cmd(session, "", allow_prompt=True)
    assert result.ok is True
    assert session.variables["yolo"] is True
    assert any("YOLO mode" in m and "ON" in m for m in session.ui.info_calls)


def test_agentic_command_emits_visible_banner():
    session = _SessionStub()
    result = agentic_cmd(session, "", allow_prompt=True)
    assert result.ok is True
    assert session.agentic is False  # toggled from True default
    assert any("Agentic mode" in m and "OFF" in m for m in session.ui.info_calls)


def test_stats_command_emits_summary_line(monkeypatch):
    # `collect_runtime_metrics` is called on the session — patch with a
    # known shape so the summary line is deterministic.
    fake_snapshot = {
        "tokens": {"input": 100, "output": 50, "total": 150, "cached": 0, "reasoning": 0},
        "ctx": {"current": 42, "maximum": 1000},
        "mode": {"name": "default"},
        "yolo": {"enabled": False},
        "plan": {"enabled": False},
    }
    monkeypatch.setattr(
        "utils.runtime_metrics.collect_runtime_metrics",
        lambda _session: fake_snapshot,
    )
    session = _SessionStub()
    result = stats_cmd(session, "", allow_prompt=True)
    assert result.ok is True
    assert session.ui.info_calls, "stats should print a summary line"
    body = session.ui.info_calls[0]
    assert "tokens" in body.lower()
    assert "100" in body  # input tokens
    assert "150" in body  # total
    assert "mode=default" in body


# ============================================================ _GenerationLive


def test_show_status_returns_a_context_manager_that_routes_deltas():
    from ui.rich_ui import RichUI, _GenerationLive

    ui = RichUI()
    cm = ui.show_status("Generating it 1/5")
    assert isinstance(cm, _GenerationLive)


def test_generation_live_routes_text_through_append_text(monkeypatch):
    """Inside the CM, `stream_assistant_delta` calls `_GenerationLive.append_text`
    instead of falling through to `console.print`."""
    from ui.rich_ui import RichUI

    ui = RichUI()
    # Avoid actually starting a Rich Live (writes to TTY).
    monkeypatch.setattr("rich.live.Live.start", lambda self: None)
    monkeypatch.setattr("rich.live.Live.stop", lambda self: None)
    monkeypatch.setattr("rich.live.Live.update", lambda self, renderable: None)

    captured_prints = []
    ui.console.print = lambda *a, **kw: captured_prints.append((a, kw))

    with ui.show_status("status text") as live:
        ui.stream_assistant_delta("Hello, ")
        ui.stream_assistant_delta("world!")
        # The buffer accumulated INSIDE the Live, not via console.print.
        assert "".join(live._text_buf) == "Hello, world!"
        # And no bare `console.print(text, end="")` happened for the deltas.
        delta_prints = [
            (a, kw)
            for (a, kw) in captured_prints
            if kw.get("end") == "" and a and "Hello" in str(a[0])
        ]
        assert delta_prints == [], (
            f"streaming delta should NOT bypass the Live; got {delta_prints!r}"
        )


def test_generation_live_thinking_buffered_separately(monkeypatch):
    from ui.rich_ui import RichUI

    ui = RichUI()
    monkeypatch.setattr("rich.live.Live.start", lambda self: None)
    monkeypatch.setattr("rich.live.Live.stop", lambda self: None)
    monkeypatch.setattr("rich.live.Live.update", lambda self, renderable: None)

    with ui.show_status("x") as live:
        ui.stream_assistant_delta("answer")
        ui.stream_thinking_delta("reasoning step 1")
        ui.stream_thinking_delta(" — step 2")
        assert "".join(live._text_buf) == "answer"
        assert "".join(live._thinking_buf) == "reasoning step 1 — step 2"


def test_generation_live_tool_calls_logged(monkeypatch):
    from ui.rich_ui import RichUI

    ui = RichUI()
    monkeypatch.setattr("rich.live.Live.start", lambda self: None)
    monkeypatch.setattr("rich.live.Live.stop", lambda self: None)
    monkeypatch.setattr("rich.live.Live.update", lambda self, renderable: None)

    with ui.show_status("x") as live:
        ui.stream_assistant_delta("Calling tools...")
        ui.stream_tool_call("read_file")
        ui.stream_tool_call("bash")
        assert live._tool_call_log == ["read_file", "bash"]


def test_generation_live_status_footer_renderable(monkeypatch):
    """The rendered Group must end with the status footer (so it stays at
    the bottom). We can't see the rendered output directly, but we can
    verify the renderable shape."""
    from ui.rich_ui import RichUI
    from rich.console import Group

    ui = RichUI()
    monkeypatch.setattr("rich.live.Live.start", lambda self: None)
    monkeypatch.setattr("rich.live.Live.stop", lambda self: None)
    monkeypatch.setattr("rich.live.Live.update", lambda self, renderable: None)

    with ui.show_status("Status footer text") as live:
        ui.stream_assistant_delta("body text")
        rendered = live._render()
        assert isinstance(rendered, Group)
        # The Group's last element corresponds to the status footer.
        # Rich's Group stores renderables in `renderables` (a tuple).
        children = list(rendered.renderables)
        assert children, "Group should not be empty after a delta"
        # The footer is either a Spinner (mid-stream) or a Text (final).
        from rich.spinner import Spinner
        from rich.text import Text
        last = children[-1]
        assert isinstance(last, (Spinner, Text))


def test_generation_live_final_render_drops_status_footer(monkeypatch):
    """`_render(final=True)` must not include the status footer. The Live
    is also `transient=True` so this only matters defensively, but keep
    the rule so the rendered Group never carries the "Generating ... it
    N/1000 | ..." line into scrollback."""
    from ui.rich_ui import RichUI
    from rich.spinner import Spinner

    ui = RichUI()
    monkeypatch.setattr("rich.live.Live.start", lambda self: None)
    monkeypatch.setattr("rich.live.Live.stop", lambda self: None)
    monkeypatch.setattr("rich.live.Live.update", lambda self, renderable: None)

    cm = ui.show_status("Generating (dummy) it 3/1000 | ctx: 1%")
    cm.__enter__()
    cm.append_text("the answer")
    cm.note_tool_call("read_file")
    rendered_final = cm._render(final=True)
    children = list(rendered_final.renderables)

    rendered_strs = [str(c) for c in children]
    assert any("the answer" in s for s in rendered_strs)
    assert any("read_file" in s for s in rendered_strs)
    assert not any(isinstance(c, Spinner) for c in children)
    assert not any("Generating" in str(c) for c in children)
    cm.__exit__(None, None, None)


def test_generation_live_is_transient_so_streamed_region_clears(monkeypatch):
    """The Live must be created with `transient=True`. The streamed
    plain-text buffer used during generation is replaced on exit with a
    properly-styled Markdown re-render (see next test), so leaving the
    raw plain-text region in scrollback would mean every turn shows the
    answer twice — once unrendered, once rendered."""
    from ui.rich_ui import RichUI

    captured = {}

    real_init = __import__("rich.live", fromlist=["Live"]).Live.__init__

    def _capture_init(self, *args, **kwargs):
        captured["transient"] = kwargs.get("transient")
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr("rich.live.Live.__init__", _capture_init)
    monkeypatch.setattr("rich.live.Live.start", lambda self: None)
    monkeypatch.setattr("rich.live.Live.stop", lambda self: None)
    monkeypatch.setattr("rich.live.Live.update", lambda self, renderable: None)

    ui = RichUI()
    cm = ui.show_status("x")
    cm.__enter__()
    cm.__exit__(None, None, None)
    assert captured.get("transient") is True


def test_generation_live_reemits_markdown_on_exit(monkeypatch):
    """On exit the Live region is transient. The accumulated assistant
    text must then be re-printed through `render_response` so it lands
    in scrollback as a rendered Markdown block — not as raw `**bold**`
    or `# header` characters."""
    from ui.rich_ui import RichUI

    monkeypatch.setattr("rich.live.Live.start", lambda self: None)
    monkeypatch.setattr("rich.live.Live.stop", lambda self: None)
    monkeypatch.setattr("rich.live.Live.update", lambda self, renderable: None)

    render_calls: list = []
    import ui.render as _render

    def _spy(text):
        render_calls.append(text)

    monkeypatch.setattr(_render, "render_response", _spy)

    ui_inst = RichUI()
    cm = ui_inst.show_status("x")
    cm.__enter__()
    cm.append_text("# heading\n\nSome **bold** text.")
    cm.__exit__(None, None, None)

    assert render_calls, "expected render_response to be called on Live exit"
    rendered = render_calls[0]
    assert "# heading" in rendered
    assert "**bold**" in rendered


def test_generation_live_emits_thinking_block_as_dim_italic(monkeypatch):
    """Thinking is captured during the Live; on exit it must be printed
    as a separate dim-italic block before the assistant text, not mixed
    in via render_response (markdown rendering on partial reasoning
    looks erratic)."""
    from ui.rich_ui import RichUI
    from rich.text import Text

    monkeypatch.setattr("rich.live.Live.start", lambda self: None)
    monkeypatch.setattr("rich.live.Live.stop", lambda self: None)
    monkeypatch.setattr("rich.live.Live.update", lambda self, renderable: None)

    ui_inst = RichUI()
    printed: list = []

    real_print = ui_inst.console.print

    def _capture(*args, **kwargs):
        printed.append(args[0] if args else None)
        return None

    monkeypatch.setattr(ui_inst.console, "print", _capture)
    cm = ui_inst.show_status("x")
    cm.__enter__()
    cm.append_thinking("Hmm, let me think about this.")
    cm.__exit__(None, None, None)

    thinking_prints = [
        p for p in printed if isinstance(p, Text) and "think about this" in str(p)
    ]
    assert thinking_prints, "expected a Text print containing the thinking content"
    assert thinking_prints[0].style == "dim italic"


def test_generation_live_clears_streamed_flag_on_enter_then_sets_on_delta(monkeypatch):
    from ui.rich_ui import RichUI

    ui = RichUI()
    monkeypatch.setattr("rich.live.Live.start", lambda self: None)
    monkeypatch.setattr("rich.live.Live.stop", lambda self: None)
    monkeypatch.setattr("rich.live.Live.update", lambda self, renderable: None)

    ui._streamed_any_text = True  # leftover from prior iteration
    cm = ui.show_status("x")
    cm.__enter__()
    assert ui._streamed_any_text is False  # cleared on enter
    ui.stream_assistant_delta("hello")
    assert ui._streamed_any_text is True  # set after first delta
    cm.__exit__(None, None, None)
    # Survives Live exit so render_message can suppress its panel.
    assert ui._streamed_any_text is True


def test_show_status_compat_update_method(monkeypatch):
    """The YOLO watcher used to call `status.update(new_message)` on the
    old `console.status` return value. Our new `_GenerationLive` exposes
    the same `update()` method for that compat path."""
    from ui.rich_ui import RichUI

    ui = RichUI()
    monkeypatch.setattr("rich.live.Live.start", lambda self: None)
    monkeypatch.setattr("rich.live.Live.stop", lambda self: None)
    monkeypatch.setattr("rich.live.Live.update", lambda self, renderable: None)

    cm = ui.show_status("Original status")
    cm.__enter__()
    assert hasattr(cm, "update")
    cm.update("Updated status")
    assert cm._status_message == "Updated status"
    cm.__exit__(None, None, None)
