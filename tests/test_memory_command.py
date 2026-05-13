"""Pin the /memory slash command: status, list, clear.

Specifically:
  * `/memory list <layer>` shows the actual injected content for each
    of the 7 hierarchical layers (L1, L1B, L2, L3, L4, L4B, L5).
  * Aliases removed in the cleanup pass (`ls`, `s`, `scratch`,
    `longterm`, `long-term`) are NOT silently accepted.
"""

import pytest

import mu.commands as mc
from core.session import Session, SessionManager
from mu.commands.memory import LIST_TARGETS, memory_cmd
from providers.base import LLMProvider, ProviderResponse


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="", parts=[])

    def upload_file(self, file_path, mime_type):
        return None


@pytest.fixture
def session():
    sm = SessionManager()
    s = Session(_DummyProvider("dummy"), False, "system instruction", sm)
    s._mcp_clients = []
    # Isolate from any prior on-disk session state.
    s.session_manager.history = []
    s.session_manager.summary_anchor = 0
    s.session_manager.conversation_summary = ""
    s.session_manager.provider_config = {"provider": "openai", "model": "gpt-4o"}
    return s


# ----------------------------------------------- list targets


def test_list_targets_export_matches_layers_and_stores():
    """Canonical list targets are exposed for the autocompleter. If a
    new layer is added (e.g. L6), this pin will flag the autocompleter
    to be updated."""
    assert set(LIST_TARGETS) == {
        "all",
        "task",
        "scratchpad",
        "L0",
        "L1",
        "L1B",
        "L2",
        "L3",
        "L4",
        "L4B",
        "L5",
    }


def test_list_all_returns_both_stores(session):
    result = memory_cmd(session, "list all", allow_prompt=False)
    assert result.ok
    assert "task_memory" in result.data
    assert "scratchpad" in result.data


def test_list_task_omits_scratchpad(session):
    result = memory_cmd(session, "list task", allow_prompt=False)
    assert result.ok
    assert "task_memory" in result.data
    assert "scratchpad" not in result.data


def test_list_scratchpad_omits_task(session):
    result = memory_cmd(session, "list scratchpad", allow_prompt=False)
    assert result.ok
    assert "scratchpad" in result.data
    assert "task_memory" not in result.data


# ----------------------------------------------- layer listing


@pytest.mark.parametrize("layer", ["L0", "L1", "L1B", "L2", "L3", "L4", "L4B", "L5"])
def test_list_each_layer_returns_content_field(session, layer):
    """Every layer ID must resolve. Content may be empty in a fresh
    session but the data shape must be consistent."""
    result = memory_cmd(session, f"list {layer}", allow_prompt=False)
    assert result.ok, f"/memory list {layer} failed: {result.message}"
    assert result.data["layer"] == layer
    assert "content" in result.data
    assert isinstance(result.data["content"], str)


def test_list_layer_is_case_insensitive(session):
    """`/memory list l1` and `/memory list L1` should both work."""
    upper = memory_cmd(session, "list L1", allow_prompt=False)
    lower = memory_cmd(session, "list l1", allow_prompt=False)
    assert upper.ok
    assert lower.ok
    assert upper.data["layer"] == lower.data["layer"] == "L1"


def test_list_l5_reflects_conversation_history(session):
    session.session_manager.history = [
        {"role": "user", "parts": [{"type": "text", "text": "ping"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "pong"}]},
    ]
    result = memory_cmd(session, "list L5", allow_prompt=False)
    assert result.ok
    body = result.data["content"]
    assert "ping" in body
    assert "pong" in body
    # The role headers should be human-readable, not JSON keys.
    assert "USER" in body
    assert "ASSISTANT" in body


def test_list_l5_is_not_raw_json_dump(session):
    """Regression-pin: the L5 view used to be a raw `json.dumps(history)`
    which suggested the harness wrapped every message in
    `{"parts": [{"type": "text", "text": "..."}]}` on the wire. It
    doesn't — each provider serializes its own way. Render a clean
    conversational view instead."""
    session.session_manager.history = [
        {"role": "user", "parts": [{"type": "text", "text": "hello"}]},
    ]
    body = memory_cmd(session, "list L5", allow_prompt=False).data["content"]
    # The clean view has no JSON noise.
    assert '"role"' not in body
    assert '"parts"' not in body
    assert '"type": "text"' not in body


def test_list_l5_summarizes_tool_calls_inline(session):
    session.session_manager.history = [
        {"role": "user", "parts": [{"type": "text", "text": "find files"}]},
        {
            "role": "assistant",
            "parts": [
                {"type": "tool_call", "tool_name": "list_dir", "tool_args": {"path": "."}},
                {"type": "tool_result", "tool_name": "list_dir", "tool_result": "a.py\nb.py"},
                {"type": "text", "text": "Two files."},
            ],
        },
    ]
    body = memory_cmd(session, "list L5", allow_prompt=False).data["content"]
    # Tool call arrow + name + JSON args.
    assert "→ list_dir(" in body
    # Tool result arrow + name + preview.
    assert "← list_dir:" in body
    assert "a.py" in body
    assert "Two files." in body


def test_list_l5_truncates_huge_tool_results(session):
    """A 50kB tool_result must not blow the panel — preview only."""
    huge = "x" * 50_000
    session.session_manager.history = [
        {
            "role": "assistant",
            "parts": [
                {"type": "tool_result", "tool_name": "read_file", "tool_result": huge},
            ],
        }
    ]
    body = memory_cmd(session, "list L5", allow_prompt=False).data["content"]
    # The full payload must NOT be in the rendered view.
    assert len(body) < 5000
    assert "…" in body  # ellipsis marker


def test_list_l2_reflects_conversation_summary(session):
    session.session_manager.conversation_summary = (
        "### Summarized conversation\n- discussed widget refactor"
    )
    result = memory_cmd(session, "list L2", allow_prompt=False)
    assert result.ok
    assert "widget refactor" in result.data["content"]


def test_list_l4b_reflects_pending_retrieved_context(session):
    session._pending_retrieved_context = "[retrieved] payments/charge.py:14 — process_card(...)"
    result = memory_cmd(session, "list L4B", allow_prompt=False)
    assert result.ok
    assert "payments/charge.py" in result.data["content"]


def test_list_l0_includes_user_system_instruction(session):
    """The user-set system_instruction should appear verbatim in L0."""
    session.system_instruction = "You are a coding assistant for project ACME."
    body = memory_cmd(session, "list L0", allow_prompt=False).data["content"]
    assert "project ACME" in body


def test_list_l0_includes_agentic_harness_when_enabled(session):
    """When agentic mode is on, L0 must include the full harness +
    mode workflow so the user can see why the context cost is high."""
    session.system_instruction = "be helpful"
    session.agentic = True
    session.variables["agent_mode"] = "default"
    body = memory_cmd(session, "list L0", allow_prompt=False).data["content"]
    # The agentic harness prompt mentions strategy mode.
    assert "CURRENT STRATEGY MODE" in body
    assert "DEFAULT" in body


def test_list_unknown_target_errors(session):
    result = memory_cmd(session, "list nope", allow_prompt=False)
    assert not result.ok
    assert "Unknown list target" in result.message


# ----------------------------------------------- alias removals


def test_ls_alias_removed(session):
    """`/memory ls` was removed in the cleanup pass."""
    result = memory_cmd(session, "ls", allow_prompt=False)
    assert not result.ok
    assert "Unknown subcommand" in result.message


def test_status_short_alias_removed(session):
    """`/memory s` was removed."""
    result = memory_cmd(session, "s", allow_prompt=False)
    assert not result.ok
    assert "Unknown subcommand" in result.message


def test_clear_scratch_alias_removed(session):
    """`/memory clear scratch` is no longer accepted — use `scratchpad`."""
    result = memory_cmd(session, "clear scratch", allow_prompt=False)
    assert not result.ok
    assert "Usage" in result.message


def test_clear_longterm_alias_removed(session):
    """`/memory clear longterm` is no longer accepted — use `task`."""
    result = memory_cmd(session, "clear longterm", allow_prompt=False)
    assert not result.ok
    result_dashed = memory_cmd(session, "clear long-term", allow_prompt=False)
    assert not result_dashed.ok


# ----------------------------------------------- still-supported clear


def test_clear_task_wipes_only_task_memory(session):
    session.task_memory.save("a fact", tags=["x"])
    session.turn_scratchpad.save("a note", tags=["y"])
    result = memory_cmd(session, "clear task", allow_prompt=False)
    assert result.ok
    assert len(session.task_memory.entries) == 0
    assert len(session.turn_scratchpad.entries) == 1  # untouched


def test_clear_scratchpad_wipes_only_scratchpad(session):
    session.task_memory.save("a fact", tags=["x"])
    session.turn_scratchpad.save("a note", tags=["y"])
    result = memory_cmd(session, "clear scratchpad", allow_prompt=False)
    assert result.ok
    assert len(session.task_memory.entries) == 1  # untouched
    assert len(session.turn_scratchpad.entries) == 0


def test_clear_all_wipes_both(session):
    session.task_memory.save("a", tags=[])
    session.turn_scratchpad.save("b", tags=[])
    result = memory_cmd(session, "clear all", allow_prompt=False)
    assert result.ok
    assert len(session.task_memory.entries) == 0
    assert len(session.turn_scratchpad.entries) == 0


# ----------------------------------------------- dispatch through registry


def test_dispatch_through_registry(session):
    result = mc.dispatch(session, "/memory list L1", allow_prompt=False)
    assert result is not None
    assert result.ok
    assert result.data["layer"] == "L1"
