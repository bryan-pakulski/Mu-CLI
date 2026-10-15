"""`/job settings` text surface and the jobs TUI Settings screen (run rules)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mu.commands import job as terminal
from mu.jobs import job_settings
from mu.ui.jobs import views
from mu.ui.jobs.app import JobsApp
from mu.ui.jobs.data import DispatchError, JobsData
from mu.ui.jobs.primitives import line_width, to_plain


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path))
    return tmp_path


class UI:
    def __init__(self):
        self.info, self.errors = [], []

    def show_info(self, text):
        self.info.append(text)

    def show_error(self, text):
        self.errors.append(text)


def run(args):
    ui = UI()
    result = terminal.job_cmd(SimpleNamespace(ui=ui), args)
    return result, ui


def _stored(home):
    return json.loads((home / "jobs" / "settings.json").read_text())


# ---------------------------------------------------------------- /job settings

def test_settings_lists_every_rule_by_section():
    result, ui = run("settings")
    assert result.ok and ui.info and not ui.errors
    text = ui.info[0]
    for section in job_settings.RULES_SCHEMA:
        assert section["title"] in text
        for field in section["fields"]:
            assert field["label"] in text and field["path"] in text
    assert "Token budget per stage: 50,000,000 tokens  (default)" in text
    assert "Tokens one stage may use across its runs in a job; then the job pauses and asks you" in text
    assert "(set)" not in text
    assert set(result.data) == {"schema", "values", "overrides"}


def test_set_parses_by_kind_and_marks_set(home):
    assert run("settings set sandbox.stage_timeout_s.implement 7,200")[0].ok
    assert run("settings set sandbox.count_cache_reads on")[0].ok
    assert run("settings set sandbox.max_cost_usd 12.5")[0].ok
    assert run("settings set sandbox.egress_extra pypi.org, *.npmjs.org")[0].ok
    assert run('settings set checks.suites [{"name": "unit", "command": "pytest -q"}]')[0].ok
    result, ui = run("settings set publish.mode gh")
    assert result.ok and ui.info[-1] == "Publish mode set to GitHub CLI."
    stored = _stored(home)
    assert stored["sandbox"]["stage_timeout_s"] == {"implement": 7200}
    assert stored["sandbox"]["count_cache_reads"] is True
    assert stored["sandbox"]["max_cost_usd"] == 12.5
    assert stored["sandbox"]["egress_extra"] == ["pypi.org", "*.npmjs.org"]
    assert stored["checks"]["suites"] == [{"name": "unit", "command": "pytest -q"}]
    assert stored["publish"]["mode"] == "gh"
    _, ui = run("settings")
    assert "Implement: 7,200 seconds  (set)" in ui.info[0]
    assert "default 3,600 seconds" in ui.info[0]
    assert "Extra egress domains: pypi.org, *.npmjs.org  (set)" in ui.info[0]


def test_reset_restores_default(home):
    run("settings set sandbox.stage_timeout_s.implement 7200")
    result, ui = run("settings reset sandbox.stage_timeout_s.implement")
    assert result.ok and ui.info[-1] == "Implement reset to the default (3,600 seconds)."
    assert "implement" not in (_stored(home).get("sandbox") or {}).get("stage_timeout_s", {})
    assert job_settings.rule("sandbox.stage_timeout_s.implement") == 3600


@pytest.mark.parametrize("args, message", [
    ("settings set sandbox.max_tokens lots", "Token budget per stage must be a whole number"),
    ("settings set sandbox.max_tokens 0", "Token budget per stage must be between 1 and 10000000000"),
    ("settings set sandbox.count_cache_reads maybe", "Count cache reads must be on or off"),
    ("settings set sandbox.memory lots", "Memory limit is not valid"),
    ("settings set publish.mode ftp", "Publish mode must be one of"),
    ("settings set checks.suites [{\"name\": \"x\"}]", "every suite needs a name and a command"),
    ("settings set checks.suites nope", "Required check suites must be JSON"),
])
def test_invalid_values_are_reported_and_not_saved(home, args, message):
    result, ui = run(args)
    assert not result.ok and message in result.message and message in ui.errors[-1]
    assert result.message.startswith("Invalid value for ")
    assert not (home / "jobs" / "settings.json").exists()


def test_unknown_path_and_usage():
    result, ui = run("settings set sandbox.timeout 5")
    assert not result.ok and "Unknown setting: sandbox.timeout" in result.message
    result, _ = run("settings set max_tokens 5")
    assert "Did you mean: sandbox.max_tokens" in result.message
    result, _ = run("settings frobnicate")
    assert not result.ok and result.message.startswith("Usage: /job settings")


def test_job_completer_offers_settings_paths():
    from mu.ui import input as ui_input
    import inspect
    src = inspect.getsource(ui_input)
    assert '"settings": {"set": dict(_job_rule_paths), "reset": dict(_job_rule_paths)}' in src


# ---------------------------------------------------------------- jobs TUI Settings screen

class SettingsData(JobsData):
    """Real settings reads/writes, no job store."""

    def __init__(self):
        super().__init__(object())

    def home(self):
        return {"needs_you": [], "running": [], "jobs": []}

    def status(self, home=None):
        return {}


def _app():
    app = JobsApp(SettingsData())
    app.refresh()
    return app


def _text(app, w=100, h=34):
    lines = views.render(app, w, h)
    assert len(lines) == h and all(line_width(l) == w for l in lines)
    return to_plain(lines)


def _type(app, text):
    for ch in text:
        app.handle_key("space" if ch == " " else ch)


def _select(app, path):
    app.cursors["settings"] = [f["path"] for f in app.settings_fields()].index(path)


def test_g_s_and_palette_open_settings_screen():
    app = _app()
    app.handle_key("g")
    app.handle_key("s")
    assert app.route["name"] == "settings"
    text = _text(app)
    assert "Settings · job run rules" in text and "Model budget" in text
    assert "› Token budget per stage" in text and "50,000,000 tokens" in text
    assert "sandbox.max_tokens" in text and "Tokens one stage may use across its runs" in text
    app.handle_key("escape")
    assert app.route["name"] == "home"
    app.handle_key(":")
    _type(app, "settings")
    app.handle_key("enter")
    assert app.route["name"] == "settings"


def test_tui_edit_inline_saves_and_reset_with_ctrl_r(home):
    app = _app()
    app.open_settings()
    _select(app, "sandbox.stage_timeout_s.implement")
    app.handle_key("enter")
    assert app.mode["kind"] == "prompt" and app.mode["text"] == "3600"
    assert "Implement (whole number seconds 1–86,400)" in _text(app)
    app.handle_key("c-u")
    _type(app, "7200")
    app.handle_key("enter")
    assert app.mode is None
    assert _stored(home)["sandbox"]["stage_timeout_s"]["implement"] == 7200
    text = _text(app)
    assert "7,200 seconds" in text and "default 3,600 seconds" in text
    assert app.current_flash() == ("✓ Implement = 7,200 seconds", "ok")
    app.handle_key("c-r")
    assert job_settings.rule_overrides() == {}
    assert app.current_flash()[0] == "✓ Implement reset to default (3,600 seconds)"


def test_tui_bool_toggles_and_invalid_value_keeps_prompt(home):
    app = _app()
    app.open_settings()
    _select(app, "sandbox.count_cache_reads")
    app.handle_key("enter")
    assert app.mode is None and _stored(home)["sandbox"]["count_cache_reads"] is True
    _select(app, "sandbox.memory")
    app.handle_key("enter")
    app.handle_key("c-u")
    _type(app, "lots")
    app.handle_key("enter")
    assert app.current_flash() == ("Memory limit is not valid", "danger")
    assert app.mode and app.mode["kind"] == "prompt" and app.mode["text"] == "lots"
    assert "memory" not in _stored(home)["sandbox"]


def test_tui_list_and_suites_parse(home):
    from mu.jobs.job_settings import set_rule
    # The editor pre-fills the current value; start from an empty list so the typed text is the
    # whole value (the default is now the package-registry list).
    set_rule("sandbox.egress_extra", [])
    app = _app()
    app.open_settings()
    _select(app, "sandbox.egress_extra")
    app.handle_key("enter")
    _type(app, "pypi.org, *.npmjs.org")
    app.handle_key("enter")
    assert _stored(home)["sandbox"]["egress_extra"] == ["pypi.org", "*.npmjs.org"]
    _select(app, "checks.suites")
    app.handle_key("enter")
    assert app.mode["text"] == "[]"
    app.handle_key("c-u")
    _type(app, '[{"name": "unit", "command": "pytest -q"}]')
    app.handle_key("enter")
    assert _stored(home)["checks"]["suites"] == [{"name": "unit", "command": "pytest -q"}]


def test_set_rule_wraps_errors_as_dispatch_error():
    with pytest.raises(DispatchError, match="unknown setting"):
        SettingsData().set_rule("nope.path", "1")


# ---------------------------------------------------------------- rows rules + toolchains

TOOLCHAIN_ROW = {"repository": "/src/app", "base_image": "rust:1.88"}


@pytest.fixture
def fake_toolchains(monkeypatch):
    """No docker: status/build come from an in-memory table."""
    from mu.jobs.sandbox import toolchain
    state = {"builds": [], "status": {}}

    def status():
        return [{**row, "tag": f"mucli-job-toolchain:{row['base_image'].replace(':', '-')}",
                 **state["status"].get(row["base_image"], {"status": "missing"})}
                for row in toolchain.toolchain_rows()]

    def build(base):
        state["builds"].append(base)
        state["status"][base] = {"status": "building", "log_tail": "Probing rust:1.88 …\nStep 2/7 : RUN apt-get\n"}
        return {"base_image": base, "tag": "mucli-job-toolchain:rust-1.88", **state["status"][base]}

    monkeypatch.setattr(toolchain, "toolchains_status", status)
    monkeypatch.setattr(toolchain, "build_in_background", build)
    return state


def test_set_rows_from_json_and_list_readably(home):
    result, ui = run("settings set sandbox.toolchains '[{\"repository\": \"/src/app\", \"base_image\": \"rust:1.88\"}]'")
    assert result.ok, result.message
    assert ui.info[-1] == "Repository toolchain images set to /src/app · rust:1.88."
    assert _stored(home)["sandbox"]["toolchains"] == [TOOLCHAIN_ROW]
    # A bare object (no quotes) is one row.
    assert run('settings set sandbox.toolchains {"repository": "/src/b", "base_image": "node:22"}')[0].ok
    assert _stored(home)["sandbox"]["toolchains"] == [{"repository": "/src/b", "base_image": "node:22"}]
    run("settings set sandbox.toolchains " + json.dumps([TOOLCHAIN_ROW, {"repository": "/src/b", "base_image": "node:22"}]))
    _, ui = run("settings")
    text = ui.info[0]
    assert "Repository toolchain images: 2 rows  (set)" in text
    assert "      - /src/app · rust:1.88" in text and "      - /src/b · node:22" in text
    assert "sandbox.toolchains · default none" in text
    assert "'repository'" not in text and "/job toolchains build <base_image>" in text
    result, ui = run("settings reset sandbox.toolchains")
    assert result.ok and ui.info[-1] == "Repository toolchain images reset to the default (none)."


def test_rows_default_listing():
    _, ui = run("settings")
    assert "Repository toolchain images: none  (default)" in ui.info[0]


@pytest.mark.parametrize("value, message", [
    ("nope", "Repository toolchain images must be JSON"),
    ('["x"]', "must be a JSON list of objects"),
    ('[{"repository": "/src/app"}]', "Base image is required"),
    ('[{"repository": "/src/app", "base_image": "bad image!"}]', "is not valid"),
])
def test_invalid_rows_are_reported_and_not_saved(home, value, message):
    result, ui = run(f"settings set sandbox.toolchains {value}")
    assert not result.ok and message in result.message and message in ui.errors[-1]
    assert not (home / "jobs" / "settings.json").exists()


def test_job_toolchains_lists_status_and_builds_in_background(home, fake_toolchains):
    result, ui = run("toolchains")
    assert result.ok and "No toolchains configured" in ui.info[-1]
    run("settings set sandbox.toolchains " + json.dumps([TOOLCHAIN_ROW]))
    result, ui = run("toolchains")
    assert result.ok and "○ not built  /src/app ← rust:1.88" in ui.info[-1]
    assert "/job toolchains build" in ui.info[-1]
    result, ui = run("toolchains build rust:1.88")
    assert result.ok and fake_toolchains["builds"] == ["rust:1.88"]
    assert "in the background" in ui.info[-1] and result.data["status"] == "building"
    result, ui = run("toolchains")
    text = ui.info[-1]
    assert "● building" in text and "Step 2/7 : RUN apt-get" in text and "run /job toolchains again" in text
    fake_toolchains["status"]["rust:1.88"] = {"status": "failed", "error": "docker build failed", "log_tail": "E: boom\n"}
    _, ui = run("toolchains")
    assert "✗ failed" in ui.info[-1] and "error: docker build failed" in ui.info[-1] and "E: boom" in ui.info[-1]


def test_job_toolchains_build_rejects_unsaved_or_invalid_images(home, fake_toolchains):
    result, ui = run("toolchains build node:22")
    assert not result.ok and "add it to Settings → Toolchains first" in ui.errors[-1]
    result, _ = run("toolchains build -bad")
    assert not result.ok and "valid base image" in result.message
    result, _ = run("toolchains build")
    assert not result.ok and result.message.startswith("Usage: /job toolchains build")
    result, _ = run("toolchains frob")
    assert not result.ok and result.message.startswith("Usage: /job toolchains")
    assert fake_toolchains["builds"] == []


def test_job_completer_offers_toolchains():
    from mu.ui import input as ui_input
    import inspect
    assert '"toolchains": {"build": None}' in inspect.getsource(ui_input)


def test_tui_rows_edit_as_prefilled_json_and_toolchain_status(home, fake_toolchains):
    app = _app()
    app.open_settings()
    _select(app, "sandbox.toolchains")
    text = _text(app, w=120, h=60)
    assert "Toolchains" in text and "images: save a row to build" in text
    app.handle_key("enter")
    assert app.mode["kind"] == "prompt" and app.mode["text"] == "[]"
    assert '"repository": …, "base_image": …' in _text(app, w=120, h=60)
    app.handle_key("c-u")
    _type(app, json.dumps([TOOLCHAIN_ROW]))
    app.handle_key("enter")
    assert app.mode is None, app.current_flash()
    assert _stored(home)["sandbox"]["toolchains"] == [TOOLCHAIN_ROW]
    assert app.current_flash() == ("✓ Repository toolchain images = /src/app · rust:1.88", "ok")
    field = app.selected_setting()
    assert field["display"] == "/src/app · rust:1.88" and json.loads(field["edit_text"]) == [TOOLCHAIN_ROW]
    text = _text(app, w=120, h=60)
    assert "○ not built  /src/app ← rust:1.88" in text
    assert ("b", "build images") in app.hints()
    app.handle_key("b")
    assert fake_toolchains["builds"] == ["rust:1.88"]
    text = _text(app, w=120, h=60)
    assert "● building" in text and "Step 2/7 : RUN apt-get" in text
    app.handle_key("b")
    assert fake_toolchains["builds"] == ["rust:1.88"]
    assert "already built or building" in app.current_flash()[0]


def test_tui_invalid_rows_keep_prompt(home):
    app = _app()
    app.open_settings()
    _select(app, "sandbox.toolchains")
    app.handle_key("enter")
    app.handle_key("c-u")
    _type(app, '[{"repository": "/src/app"}]')
    app.handle_key("enter")
    assert app.current_flash() == ("Repository toolchain images: Base image is required", "danger")
    assert app.mode and app.mode["text"] == '[{"repository": "/src/app"}]'
    assert not (home / "jobs" / "settings.json").exists()
