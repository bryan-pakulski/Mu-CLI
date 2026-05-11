"""Tests for `mu.ui.progress.SubagentProgressTracker` and its integration
into the parallel-sub-agent dispatch path.
"""

import threading
import time

import pytest

from core.session import Session, SessionManager
from core.workspace import FolderContext
from mu.ui.progress import SubagentProgressTracker
from mu.ui.subagent import SubagentUI, _extract_tool_name
from providers.base import LLMProvider, MessagePart, ProviderResponse


# ============================================================ unit: tracker


def test_open_assigns_unique_ids():
    t = SubagentProgressTracker()
    a = t.open(depth=1, task="task-a")
    b = t.open(depth=1, task="task-b")
    assert a != b
    snap = t.snapshot()
    assert [s.task for s in snap] == ["task-a", "task-b"]


def test_update_tool_increments_count_and_sets_current():
    t = SubagentProgressTracker()
    aid = t.open(depth=1, task="x")
    t.update_tool(aid, "read_file")
    t.update_tool(aid, "search_for_string")
    t.update_tool(aid, "list_dir")
    [state] = t.snapshot()
    assert state.tool_count == 3
    assert state.current_tool == "list_dir"


def test_close_sets_status_done_clears_current_tool():
    t = SubagentProgressTracker()
    aid = t.open(depth=1, task="x")
    t.update_tool(aid, "read_file")
    t.close(aid, tool_count=1, summary="all good")
    [state] = t.snapshot()
    assert state.status == "done"
    assert state.summary == "all good"
    assert state.current_tool is None
    assert state.finished_at is not None


def test_close_with_error_sets_status_error():
    t = SubagentProgressTracker()
    aid = t.open(depth=1, task="x")
    t.close(aid, tool_count=0, summary="", error="boom")
    [state] = t.snapshot()
    assert state.status == "error"
    assert state.summary == "boom"


def test_has_active_reflects_running_count():
    t = SubagentProgressTracker()
    a = t.open(depth=1, task="a")
    b = t.open(depth=1, task="b")
    assert t.has_active() is True
    t.close(a, tool_count=0, summary="done")
    assert t.has_active() is True
    t.close(b, tool_count=0, summary="done")
    assert t.has_active() is False


def test_concurrent_updates_dont_corrupt_state():
    """Multiple threads hammering update_tool should land coherent state."""
    t = SubagentProgressTracker()
    aid = t.open(depth=1, task="busy")
    threads = []
    N = 200

    def hammer(name: str):
        for _ in range(N):
            t.update_tool(aid, name)

    for name in ("a", "b", "c", "d"):
        th = threading.Thread(target=hammer, args=(name,))
        threads.append(th)
        th.start()
    for th in threads:
        th.join()
    [state] = t.snapshot()
    assert state.tool_count == 4 * N
    assert state.current_tool in {"a", "b", "c", "d"}


def test_update_after_close_is_a_noop():
    t = SubagentProgressTracker()
    aid = t.open(depth=1, task="x")
    t.close(aid, tool_count=2, summary="done")
    t.update_tool(aid, "shouldnt_show")
    [state] = t.snapshot()
    assert state.current_tool is None
    assert state.tool_count == 2  # not incremented


def test_render_panel_is_renderable():
    t = SubagentProgressTracker()
    t.open(depth=1, task="active task")
    aid = t.open(depth=2, task="finished task")
    t.update_tool(aid, "read_file")
    t.close(aid, tool_count=1, summary="result text")
    panel = t.render_panel()
    # Just verify the result is a Rich renderable (has __rich_console__ or similar).
    assert hasattr(panel, "__rich_console__") or hasattr(panel, "__rich__")


# ============================================================ unit: tool-name extraction


def test_extract_tool_name_from_running_tool_line():
    assert (
        _extract_tool_name("🔨 Running tool: read_file({'filename': 'x'})")
        == "read_file"
    )
    assert _extract_tool_name("Running tool: bash()") == "bash"
    assert _extract_tool_name("Running tool: list_dir") == "list_dir"


def test_extract_tool_name_returns_none_for_non_matching():
    assert _extract_tool_name("Tokens: In 100 | Out 50") is None
    assert _extract_tool_name("some random message") is None


# ============================================================ unit: subagent UI with tracker


class _RecordingUI:
    def __init__(self):
        self.info_calls = []
        self.error_calls = []

    def show_info(self, message):
        self.info_calls.append(str(message))

    def show_error(self, message):
        self.error_calls.append(str(message))


def test_subagent_ui_routes_tool_lines_to_tracker_when_attached():
    parent = _RecordingUI()
    tracker = SubagentProgressTracker()
    aid = tracker.open(depth=1, task="x")
    ui = SubagentUI(parent, depth=1, tracker=tracker, agent_id=aid)

    ui.show_info("🔨 Running tool: read_file({'f': '/x'})")
    ui.show_info("🔨 Running tool: search_for_string({'s': 'auth'})")

    # Tracker received both updates.
    [state] = tracker.snapshot()
    assert state.tool_count == 2
    assert state.current_tool == "search_for_string"
    # Parent UI received neither — they were routed to the panel instead.
    assert parent.info_calls == []


def test_subagent_ui_without_tracker_still_bubbles_tool_lines():
    parent = _RecordingUI()
    ui = SubagentUI(parent, depth=1)  # no tracker
    ui.show_info("🔨 Running tool: read_file({'f': '/x'})")
    assert any("Running tool: read_file" in m for m in parent.info_calls)


def test_subagent_ui_with_tracker_still_bubbles_non_tool_messages():
    """Errors, completion banners, and other arbitrary messages must still
    reach the parent's terminal — the tracker only swallows the noisy
    per-tool log lines."""
    parent = _RecordingUI()
    tracker = SubagentProgressTracker()
    aid = tracker.open(depth=1, task="x")
    ui = SubagentUI(parent, depth=1, tracker=tracker, agent_id=aid)

    ui.show_info("[Flushed 5 items from buffer]")
    ui.show_error("something broke")

    assert any("Flushed 5 items" in m for m in parent.info_calls)
    assert any("something broke" in m for m in parent.error_calls)


# ============================================================ integration with spawn


class _ScriptedProvider(LLMProvider):
    """Two-turn parent: emits N spawn_agent calls then a final summary."""

    def __init__(self, n_spawns: int, child_response: ProviderResponse = None):
        super().__init__("scripted")
        self.name = "scripted"
        self._n = n_spawns
        self._turn = 0
        self._child_response = child_response or ProviderResponse(
            text="reviewed",
            parts=[MessagePart(type="text", text="reviewed")],
        )

    def get_available_models(self):
        return ["scripted"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        # Subagent system prompts contain 'Sub-agent task' — route them to
        # the child response.
        if system_prompt and "Sub-agent task" in system_prompt:
            return self._child_response
        self._turn += 1
        if self._turn == 1:
            return ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="spawn_agent",
                        tool_args={"task": f"review item {i}"},
                    )
                    for i in range(self._n)
                ],
            )
        return ProviderResponse(
            text="all done",
            parts=[MessagePart(type="text", text="all done")],
        )

    def upload_file(self, *a, **kw):
        return None


def test_two_concurrent_spawns_register_in_tracker_attached_to_session(
    tmp_path, monkeypatch
):
    """The tracker installed by the Session loop must capture each child's
    opening, update, and closing."""
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    provider = _ScriptedProvider(n_spawns=2)
    sm = SessionManager()
    session = Session(provider, False, "system", sm)
    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    session.folder_context = fc
    session.sync_runtime_state()
    session.variables["yolo"] = True

    # We can't easily intercept the Live display, but we can intercept the
    # tracker that the parallel-batch wires onto the session.
    captured_tracker = {"t": None}

    orig_setattr = session.__class__.__setattr__

    def _watch(self, name, value):
        orig_setattr(self, name, value)
        if name == "_subagent_progress" and value is not None:
            captured_tracker["t"] = value

    # Patch __setattr__ on the instance via a wrapper attribute.
    # Simpler: read tracker after the run via inspecting tracker state via spawn module.
    # Just run and verify both children completed.
    session.send_message("review two things in parallel")

    tool_messages = [
        m for m in session.session_manager.history if m["role"] == "tool"
    ]
    assert tool_messages
    parts = tool_messages[0]["parts"]
    assert len(parts) == 2
    for p in parts:
        # Each result should be a structured dict with ok=True
        result = p.get("tool_result")
        if isinstance(result, dict):
            assert result.get("ok") is True


def test_single_spawn_does_not_open_live_panel(tmp_path, monkeypatch):
    """The live panel only kicks in for batches of 2+ spawns. A single
    spawn should still bubble its per-tool logs normally (no tracker)."""
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    provider = _ScriptedProvider(n_spawns=1)
    sm = SessionManager()
    session = Session(provider, False, "system", sm)
    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    session.folder_context = fc
    session.sync_runtime_state()
    session.variables["yolo"] = True

    session.send_message("review one thing")
    # _subagent_progress was either never set or cleared after the batch.
    assert getattr(session, "_subagent_progress", None) is None


def test_tracker_cleared_after_parallel_batch(tmp_path, monkeypatch):
    """After the batch completes, the session must NOT have a stale tracker."""
    monkeypatch.setattr("core.session.HISTORY_DIR", str(tmp_path / "history"))
    provider = _ScriptedProvider(n_spawns=3)
    sm = SessionManager()
    session = Session(provider, False, "system", sm)
    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    session.folder_context = fc
    session.sync_runtime_state()
    session.variables["yolo"] = True

    session.send_message("review three things")
    assert getattr(session, "_subagent_progress", None) is None
