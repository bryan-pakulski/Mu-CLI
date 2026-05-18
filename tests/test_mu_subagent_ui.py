"""Tests for `mu.ui.subagent.SubagentUI` and its integration with `spawn_agent`.

These pin: that the user sees the spawn happen, sees each tool call the
child makes, and sees the completion summary — instead of staring at a
frozen REPL.
"""

import pytest

from mu.session.session import Session, SessionManager
from mu.workspace.folder_context import FolderContext
from mu.tools import build_tool_context, execute
from mu.ui.subagent import SubagentUI
from providers.base import LLMProvider, MessagePart, ProviderResponse


class _RecordingUI:
    """Captures every UI method call so tests can assert on them."""

    def __init__(self):
        self.info_calls: list = []
        self.error_calls: list = []
        self.status_messages: list = []
        self.diffs: list = []
        self.tool_results: list = []
        self.messages: list = []
        self.approval_called: bool = False

    def show_info(self, message):
        self.info_calls.append(str(message))

    def show_error(self, message):
        self.error_calls.append(str(message))

    def show_status(self, message):
        self.status_messages.append(str(message))

        class _CM:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        return _CM()

    def show_diff(self, filename, original, modified):
        self.diffs.append(str(filename))

    def show_tool_result(self, result):
        self.tool_results.append(str(result))

    def render_message(self, role, content, model_name=None):
        self.messages.append((role, str(content)[:100]))

    def request_tool_approval(self, **kwargs):
        self.approval_called = True
        return "y", None

    def set_variables(self, variables_dict):
        pass


# ---------------------------------------------------------------- wrapper unit


def test_show_info_prefixes_with_depth():
    parent = _RecordingUI()
    ui = SubagentUI(parent, depth=1)
    ui.show_info("running tool: read_file")
    assert len(parent.info_calls) == 1
    msg = parent.info_calls[0]
    assert "[subagent d=1]" in msg
    assert "running tool: read_file" in msg


def test_show_error_uses_parent_error_channel():
    parent = _RecordingUI()
    ui = SubagentUI(parent, depth=2)
    ui.show_error("boom")
    assert parent.error_calls and "[subagent d=2]" in parent.error_calls[0]
    assert "boom" in parent.error_calls[0]


def test_show_status_does_not_open_nested_spinner():
    parent = _RecordingUI()
    ui = SubagentUI(parent, depth=1)
    cm = ui.show_status("Applying patch")
    # CM must be safe to enter / exit and is a no-op
    with cm as ctx:
        assert ctx is cm or ctx is not None
    # Status surfaces as a one-line info update, NOT to parent's status spinner
    assert parent.status_messages == []
    assert any("Applying patch" in m for m in parent.info_calls)


def test_high_frequency_generating_status_is_suppressed():
    parent = _RecordingUI()
    ui = SubagentUI(parent, depth=1)
    cm = ui.show_status("Generating (model) it 3/25 | ctx: 50%")
    with cm:
        pass
    # 'Generating ...' lines are intentionally suppressed to avoid flooding
    assert parent.info_calls == []


def test_per_iteration_token_lines_are_suppressed():
    """Each agent iter logs a 'Tokens: In X | Out Y' line — too noisy for
    the subagent path. The spawn_agent end banner reports the cumulative
    total once, which is enough."""
    parent = _RecordingUI()
    ui = SubagentUI(parent, depth=1)
    ui.show_info("Tokens: In 100 | Out 50 | Total 150")
    ui.show_info("Final session tokens: In 100 | Out 50 | Total 150 | Cost: $0.001")
    ui.show_info("🔨 Running tool: read_file({'filename': '/x'})")
    # Only the tool-call line should reach the parent.
    assert len(parent.info_calls) == 1
    assert "Running tool: read_file" in parent.info_calls[0]


def test_none_status_message_is_suppressed():
    parent = _RecordingUI()
    ui = SubagentUI(parent, depth=1)
    with ui.show_status(None):
        pass
    with ui.show_status(""):
        pass
    assert parent.info_calls == []


def test_silenced_methods_do_not_propagate():
    parent = _RecordingUI()
    ui = SubagentUI(parent, depth=1)
    ui.show_diff("foo.py", "a", "b")
    ui.show_tool_result("some result")
    ui.render_message("assistant", "final answer", "model-x")
    assert parent.diffs == []
    assert parent.tool_results == []
    assert parent.messages == []


def test_request_tool_approval_auto_grants():
    parent = _RecordingUI()
    ui = SubagentUI(parent, depth=1)
    choice, reason = ui.request_tool_approval(tool_name="x")
    assert choice == "y"
    assert reason is None
    # The parent UI's approval prompt MUST NOT have been called
    assert parent.approval_called is False


def test_nested_subagent_forwards_to_root_without_stacking_prefix():
    real_ui = _RecordingUI()
    depth1 = SubagentUI(real_ui, depth=1)
    depth2 = SubagentUI(depth1, depth=2)
    depth2.show_info("nested call")
    # Only one message reached the root, and it uses d=2 not stacked prefixes.
    assert len(real_ui.info_calls) == 1
    msg = real_ui.info_calls[0]
    assert "[subagent d=2]" in msg
    assert "[subagent d=1]" not in msg


def test_subagent_ui_safe_with_none_parent():
    ui = SubagentUI(None, depth=1)
    # Must not raise
    ui.show_info("anything")
    ui.show_error("anything")
    with ui.show_status("any"):
        pass


# -------------------------------------------------------- spawn integration


class _ScriptedProvider(LLMProvider):
    def __init__(self, responses):
        super().__init__("scripted-model")
        self.name = "scripted"
        self.queue = list(responses)

    def get_available_models(self):
        return ["scripted-model"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        if not self.queue:
            return ProviderResponse(text="ok", parts=[MessagePart(type="text", text="ok")])
        return self.queue.pop(0)

    def upload_file(self, *a, **kw):
        return None


def _build_parent(tmp_path, provider, ui, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    parent = Session(provider, False, "system", SessionManager(), ui=ui)
    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    parent.folder_context = fc
    return parent


def test_spawn_emits_start_and_end_banners_to_parent_ui(tmp_path, monkeypatch):
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="all done",
                parts=[MessagePart(type="text", text="all done")],
                input_tokens=5,
                output_tokens=5,
                total_tokens=10,
            )
        ]
    )
    parent_ui = _RecordingUI()
    parent = _build_parent(tmp_path, provider, parent_ui, monkeypatch)

    execute(
        "spawn_agent",
        {"task": "summarise the README"},
        build_tool_context(
            folder_context=parent.folder_context,
            ui=parent_ui,
            variables=parent.variables,
            session=parent,
        ),
    )

    # Start banner
    start_lines = [m for m in parent_ui.info_calls if "Spawning subagent" in m]
    assert start_lines, f"no start banner in {parent_ui.info_calls!r}"
    assert "summarise the README" in start_lines[0]

    # End banner
    end_lines = [m for m in parent_ui.info_calls if "Subagent" in m and "done" in m]
    assert end_lines, f"no end banner in {parent_ui.info_calls!r}"
    assert "tool call" in end_lines[0]


def test_spawn_forwards_child_tool_call_info_to_parent_ui(tmp_path, monkeypatch):
    # Child plan: one tool call (read_file), then final text.
    target = tmp_path / "data.txt"
    target.write_text("payload")
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="",
                parts=[
                    MessagePart(
                        type="tool_call",
                        tool_name="read_file",
                        tool_args={"filename": str(target)},
                    )
                ],
            ),
            ProviderResponse(
                text="found payload",
                parts=[MessagePart(type="text", text="found payload")],
            ),
        ]
    )
    parent_ui = _RecordingUI()
    parent = _build_parent(tmp_path, provider, parent_ui, monkeypatch)

    execute(
        "spawn_agent",
        {"task": "read the file"},
        build_tool_context(
            folder_context=parent.folder_context,
            ui=parent_ui,
            variables=parent.variables,
            session=parent,
        ),
    )

    # The child's "Running tool: read_file(...)" log line should have
    # bubbled to the parent UI, prefixed with the depth label.
    tool_call_lines = [
        m
        for m in parent_ui.info_calls
        if "[subagent d=1]" in m and "Running tool" in m and "read_file" in m
    ]
    assert tool_call_lines, (
        f"expected a [subagent d=1] 'Running tool: read_file...' line in parent UI; "
        f"got: {parent_ui.info_calls!r}"
    )


def test_spawn_failure_emits_error_banner(tmp_path, monkeypatch):
    """If the child raises, the parent UI gets an error line, not silence."""

    class _BoomProvider(LLMProvider):
        name = "boom"
        def get_available_models(self):
            return ["x"]
        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            raise RuntimeError("planned crash")
        def upload_file(self, *a, **kw):
            return None

    parent_ui = _RecordingUI()
    parent = _build_parent(tmp_path, _BoomProvider("x"), parent_ui, monkeypatch)

    res = execute(
        "spawn_agent",
        {"task": "boom"},
        build_tool_context(
            folder_context=parent.folder_context,
            ui=parent_ui,
            variables=parent.variables,
            session=parent,
        ),
    )

    assert res["ok"] is False
    assert any("FAILED" in m for m in parent_ui.error_calls)
