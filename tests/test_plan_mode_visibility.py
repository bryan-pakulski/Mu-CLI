"""Tests that plan mode is visually surfaced across the REPL UI:

  * `collect_runtime_metrics` exposes a `plan.enabled` field.
  * `build_live_status_line` prepends a "🔒 PLAN" marker when on.
  * `InputHandler.build_prompt_markup` adds a high-contrast plan indicator.
  * `InputHandler.build_input_toolbar_text` adds a plan-mode toolbar line.
  * `/plan on|off` emits a visible banner via `ui.show_info`.
"""

import pytest

from mu.session.session import Session, SessionManager
from mu.commands.mode import plan_cmd
from providers.base import LLMProvider, ProviderResponse
from mu.ui.input import InputHandler
from utils.runtime_metrics import build_live_status_line, collect_runtime_metrics


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="", parts=[])

    def upload_file(self, *a, **kw):
        return None


@pytest.fixture
def session(tmp_path, monkeypatch):
    # Isolate from any default-session state other tests might have
    # persisted in the shared MUCLI_HOME — the `/plan on` test below saves
    # `plan_mode=True` and that state would otherwise leak across runs.
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager()
    return Session(_DummyProvider("dummy"), False, "system", sm)


# ============================================================ metrics


def test_collect_runtime_metrics_exposes_plan_flag(session):
    session.variables["plan_mode"] = False
    metrics = collect_runtime_metrics(session)
    assert "plan" in metrics
    assert metrics["plan"]["enabled"] is False

    session.variables["plan_mode"] = True
    metrics = collect_runtime_metrics(session)
    assert metrics["plan"]["enabled"] is True


def test_status_line_lacks_plan_marker_when_off(session):
    session.variables["plan_mode"] = False
    line = build_live_status_line(session)
    assert "🔒 PLAN" not in line
    assert "yolo:off" in line  # baseline sanity


def test_status_line_prepends_plan_marker_when_on(session):
    session.variables["plan_mode"] = True
    line = build_live_status_line(session)
    assert "🔒 PLAN" in line
    # The PLAN marker must come BEFORE the yolo indicator so it's the first
    # thing the user sees on a glance.
    assert line.index("🔒 PLAN") < line.index("yolo:")


# ============================================================ input prompt


def test_input_handler_reads_plan_mode_via_variables():
    handler = InputHandler()
    handler.set_variables({"plan_mode": False})
    assert handler.is_plan_mode_enabled() is False
    handler.set_variables({"plan_mode": True})
    assert handler.is_plan_mode_enabled() is True


def test_prompt_markup_without_plan_mode():
    handler = InputHandler()
    handler.set_variables({})
    markup = handler.build_prompt_markup(
        session_name="test", staged_files=[]
    )
    assert "plan-indicator" not in markup
    assert "PLAN MODE" not in markup


def test_prompt_markup_with_plan_mode_includes_indicator():
    handler = InputHandler()
    handler.set_variables({"plan_mode": True})
    markup = handler.build_prompt_markup(
        session_name="test", staged_files=[]
    )
    assert "plan-indicator" in markup
    assert "PLAN MODE" in markup
    assert "🔒" in markup


def test_toolbar_text_includes_plan_mode_when_on():
    handler = InputHandler()
    handler.set_variables({"plan_mode": True})
    text = handler.build_input_toolbar_text()
    assert "PLAN MODE" in text


def test_toolbar_text_omits_plan_mode_when_off():
    handler = InputHandler()
    handler.set_variables({"plan_mode": False})
    text = handler.build_input_toolbar_text()
    assert "PLAN MODE" not in text


# ============================================================ /plan banner


class _RecordingUI:
    def __init__(self):
        self.info_calls = []
        self.show_error_calls = []
        self.variables_set = None

    def show_info(self, message):
        self.info_calls.append(str(message))

    def show_error(self, message):
        self.show_error_calls.append(str(message))

    def set_variables(self, variables):
        self.variables_set = variables


def test_plan_on_emits_enable_banner(session):
    session.ui = _RecordingUI()
    session.variables["plan_mode"] = False
    result = plan_cmd(session, "on", allow_prompt=True)
    assert result.ok is True
    assert session.variables["plan_mode"] is True
    banners = [m for m in session.ui.info_calls if "PLAN MODE ENABLED" in m]
    assert banners, f"expected enable banner in {session.ui.info_calls!r}"
    assert "🔒" in banners[0]


def test_plan_off_emits_disable_banner(session):
    session.ui = _RecordingUI()
    session.variables["plan_mode"] = True
    result = plan_cmd(session, "off", allow_prompt=True)
    assert result.ok is True
    assert session.variables["plan_mode"] is False
    banners = [m for m in session.ui.info_calls if "PLAN MODE DISABLED" in m]
    assert banners, f"expected disable banner in {session.ui.info_calls!r}"


def test_plan_toggle_propagates_to_ui_variables(session):
    """After /plan, the UI's variables snapshot must reflect the new value
    so the prompt prefix picks it up on the next prompt."""
    session.ui = _RecordingUI()
    session.variables["plan_mode"] = False
    plan_cmd(session, "on", allow_prompt=True)
    assert session.ui.variables_set is session.variables
    assert session.ui.variables_set.get("plan_mode") is True
