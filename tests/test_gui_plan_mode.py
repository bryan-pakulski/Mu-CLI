"""Plan mode is a first-class session variable for the GUI and mobile: listed,
settable through the variables API (same effect as `/plan`), reported in the
stats payload, and announced live with a `plan_mode_changed` event."""
import asyncio
import threading
from types import SimpleNamespace

from mu.agent.hooks import HookContext
from mu.agent.plan_mode import _block_write_tools, set_plan_mode
from mu.commands.mode import plan_cmd
from mu.gui.routers import inspector
from utils.config import DEFAULT_VARIABLES, VARIABLE_SCHEMA, validate_and_cast


class _PublishingUI:
    """Duck-types WebUI: records published events and banners."""

    def __init__(self):
        self.events = []
        self.info = []
        self.variables = None

    def publish_event(self, event):
        self.events.append(event)

    def show_info(self, message):
        self.info.append(message)

    def set_variables(self, variables):
        self.variables = variables


def _request(session):
    lock = threading.Lock()
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        session_by_name=lambda *a: session, session_lock_for=lambda *a: lock)))


def _session(ui=None):
    return SimpleNamespace(
        variables=dict(DEFAULT_VARIABLES),
        session_manager=SimpleNamespace(save_history=lambda *a: None, token_counts={}, task_memory=None),
        folder_context=None,
        ui=ui,
    )


def test_plan_mode_is_in_the_schema_with_help():
    assert VARIABLE_SCHEMA["plan_mode"] == {"type": bool, "default": False}
    assert DEFAULT_VARIABLES["plan_mode"] is False
    assert validate_and_cast("plan_mode", "on") is True
    assert "write tools are blocked" in inspector._VARIABLE_HELP["plan_mode"]


def test_plan_mode_is_listed_in_the_behavior_group():
    data = asyncio.run(inspector.list_variables(_request(_session())))
    groups = {g["name"]: {v["key"]: v for v in g["variables"]} for g in data["groups"]}
    entry = groups["behavior"]["plan_mode"]
    assert entry["type"] == "bool" and entry["value"] is False
    assert "plan_mode" not in groups.get("other", {})


def test_post_sets_plan_mode_and_publishes_event():
    ui = _PublishingUI()
    session = _session(ui)
    result = asyncio.run(inspector.set_variable("plan_mode", _request(session), {"value": True}, session=session))
    assert result == {"ok": True, "key": "plan_mode", "value": True}
    assert session.variables["plan_mode"] is True
    assert ui.events[-1] == {"kind": "plan_mode_changed", "plan_mode": True}
    assert ui.variables is session.variables
    # Web clients get the event, not a Rich-markup banner.
    assert ui.info == []

    asyncio.run(inspector.unset_variable("plan_mode", _request(session), session=session))
    assert session.variables["plan_mode"] is False
    assert ui.events[-1] == {"kind": "plan_mode_changed", "plan_mode": False}


def test_setting_via_api_blocks_writes_through_the_hook():
    session = _session(_PublishingUI())
    asyncio.run(inspector.set_variable("plan_mode", _request(session), {"value": "true"}, session=session))
    blocked = _block_write_tools(HookContext(point="pre_tool", session=session, tool_name="write_file"))
    assert blocked is not None and blocked.payload["error_code"] == "plan_mode_blocked"
    assert _block_write_tools(HookContext(point="pre_tool", session=session, tool_name="read_file")) is None

    asyncio.run(inspector.set_variable("plan_mode", _request(session), {"value": False}, session=session))
    assert _block_write_tools(HookContext(point="pre_tool", session=session, tool_name="write_file")) is None


def test_slash_command_uses_the_shared_setter_and_event():
    ui = _PublishingUI()
    session = _session(ui)
    result = plan_cmd(session, "on")
    assert result.data == {"plan_mode": True}
    assert ui.events == [{"kind": "plan_mode_changed", "plan_mode": True}]


def test_set_plan_mode_shows_terminal_banner_without_publisher():
    class _TerminalUI:
        def __init__(self):
            self.info = []

        def show_info(self, message):
            self.info.append(message)

    session = _session(_TerminalUI())
    set_plan_mode(session, True)
    assert any("PLAN MODE ENABLED" in m for m in session.ui.info)


def test_stats_report_plan_mode():
    session = _session()
    session.variables["plan_mode"] = True
    session.provider = SimpleNamespace(model_name="m", name="p")
    sm = SimpleNamespace(
        token_counts={}, current_session_name="s", history=[],
        task_memory=SimpleNamespace(entries=[], status_counts=lambda: {}),
        turn_scratchpad=SimpleNamespace(entries=[]),
    )
    session.session_manager = sm
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_by_name=lambda *a: session)))
    import utils.model_pricing as pricing

    original = pricing.session_cost_summary
    pricing.session_cost_summary = lambda s: {"cost_usd": 0.0, "source": "none"}
    try:
        data = asyncio.run(inspector.get_stats(request))
    finally:
        pricing.session_cost_summary = original
    assert data["plan_mode"] is True
