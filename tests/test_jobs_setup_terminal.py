"""Jobs Setup + Doctor in the terminal: the dashboard screens (g u / g d) and `/job setup` /
`/job doctor`. No Docker: the core setup checks and doctor are monkeypatched."""
from types import SimpleNamespace

import pytest

from mu.commands import job as job_commands
from mu.jobs import harness, setup as jobs_setup
from mu.ui.jobs import views
from mu.ui.jobs.app import JobsApp
from mu.ui.jobs.data import DispatchError, JobsData
from mu.ui.jobs.primitives import to_plain


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    for key in harness.SECRET_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(jobs_setup, "_setup", None)  # fresh process-wide Setup per test
    return tmp_path


def _steps(**states):
    out = []
    for meta in jobs_setup.STEPS:
        state = states.get(meta["id"], "ok")
        out.append({**meta, "optional": bool(meta.get("optional")), "checking": False, "state": state,
                    "detail": f"{meta['id']} is {state}", "actions": []})
    return out


class Data(JobsData):
    """Scripted setup/doctor backend for the dashboard."""

    def __init__(self, *, needs=True):
        super().__init__(object())
        self.needs = needs
        self.calls = []
        self.finished = False
        self.vm = {"steps": _steps(runtime="todo", claude="warn"), "done": 10, "total": 12,
                   "blocking": ["Job runtime image"], "can_finish": False, "job": None}
        step = {s["id"]: s for s in self.vm["steps"]}
        step["runtime"]["actions"] = [{"id": "build", "label": "Build the runtime image", "kind": "job"}]
        step["claude"]["actions"] = [
            {"id": "setup-token", "label": "Create a long-lived token", "kind": "terminal", "command": "claude setup-token"},
            {"id": "paste-token", "label": "Paste a Claude OAuth token", "kind": "prompts",
             "prompts": [{"label": "CLAUDE_CODE_OAUTH_TOKEN (hidden)", "initial": "", "secret": True}]}]
        step["models"]["actions"] = [{"id": "settings", "label": "Open Job settings", "kind": "nav", "nav": "settings"}]
        step["git"]["actions"] = [{"id": "identity", "label": "Set name and email", "kind": "prompts",
                                   "prompts": [{"label": "Your name", "initial": "Ada"}, {"label": "Your email", "initial": ""}]}]
        step["docker"]["actions"] = [{"id": "recheck", "label": "Check again", "kind": "run", "confirm": "Really?"}]

    def home(self):
        return {"needs_you": [], "running": [], "jobs": []}

    def status(self, home=None):
        return {}

    def settings(self):
        return {"sections": [], "path": ""}

    def toolchains(self):
        return []

    def needs_setup(self):
        return self.needs

    def setup(self, *, recheck=False):
        self.calls.append(("setup", recheck))
        return dict(self.vm)

    def doctor(self, *, deep=False):
        self.calls.append(("doctor", deep))
        return [{"name": "docker", "status": "ok", "detail": "Docker 29"},
                {"name": "runtime_image", "status": "fail", "detail": "rebuild it"},
                {"name": "codex_credentials", "status": "warn", "detail": "login"}] + (
            [{"name": "sandbox_selftest", "status": "ok", "detail": "PASS: 108/108 checks"}] if deep else [])

    def setup_action(self, step, action, inputs=None, *, on_line=None):
        self.calls.append(("action", step, action, list(inputs or [])))
        if action == "build":
            for line in ("#1 building", "#11 DONE"):
                on_line(line)
            self.vm = {**self.vm, "job": {"step": step, "ok": True, "running": False, "lines": ["#11 DONE"]}}
            return {"ok": True, "message": "started"}
        if action == "boom":
            raise DispatchError("it broke")
        return {"ok": True, "message": f"{action} done"}

    def setup_finish(self):
        if not self.vm.get("can_finish"):
            raise DispatchError("still to do")
        self.finished = True
        return {**self.vm, "completed_at": 1}


def _app(**kwargs):
    data = Data(**kwargs)
    app = JobsApp(data)
    app.run_background = lambda work: work()  # synchronous for tests
    app.refresh()
    return app, data


def _goto(app, step_id):
    app.setup_cursor = next(i for i, s in enumerate(app.setup_steps()) if s["id"] == step_id)


def test_home_hint_points_to_setup_until_finished():
    app, _ = _app(needs=True)
    text = to_plain(views.render(app, 120, 30))
    assert "Jobs setup isn't finished" in text and "g u" in text
    done, _ = _app(needs=False)
    assert "Jobs setup isn't finished" not in to_plain(views.render(done, 120, 30))


def test_g_u_opens_the_checklist_with_states_and_actions():
    app, data = _app()
    app.handle_key("g")
    app.handle_key("u")
    assert app.route["name"] == "setup" and ("setup", True) in data.calls
    text = to_plain(views.render(app, 140, 40))
    assert "Setup" in text and "10/12 done" in text and "Job runtime image" in text
    assert "○" in text and "To do: Job runtime image" in text
    _goto(app, "runtime")
    text = to_plain(views.render(app, 140, 40))
    assert "Build the runtime image" in text and "runtime is todo" in text
    assert ("enter", "actions") in app.hints() and ("r", "check all again") in app.hints()
    app.handle_key("r")
    assert data.calls.count(("setup", True)) == 2


def test_job_action_streams_its_log_and_reports():
    app, data = _app()
    app.open_setup()
    _goto(app, "runtime")
    repaints = []
    app.on_change = lambda: repaints.append(1)
    app.handle_key("enter")
    assert app.setup_focus == "actions"
    app.handle_key("enter")
    assert ("action", "runtime", "build", []) in data.calls
    assert app.setup_job["lines"] == ["#1 building", "#11 DONE"] and app.setup_job["ok"] is True
    assert not app.setup_job["running"] and len(repaints) >= 2
    assert app.current_flash()[0].startswith("✓ Build the runtime image")
    assert "#11 DONE" in to_plain(views.render(app, 140, 40))


def test_running_job_shows_live_and_blocks_a_second_one():
    app, data = _app()
    app.open_setup()
    pending = []
    app.run_background = pending.append  # don't run yet: the job stays "running"
    _goto(app, "runtime")
    app.handle_key("enter")
    app.handle_key("enter")
    assert app.setup_job["running"] and "running…" in to_plain(views.render(app, 140, 40))
    app.handle_key("enter")
    assert "still running" in app.current_flash()[0] and len(pending) == 1
    pending[0]()
    assert app.setup_job["ok"] is True


def test_secret_prompt_is_masked_and_never_rendered():
    app, data = _app()
    app.open_setup()
    _goto(app, "claude")
    app.handle_key("enter")
    app.handle_key("down")  # paste-token
    app.handle_key("enter")
    assert app.mode["kind"] == "prompt" and app.mode["secret"]
    for ch in "sk-ant-oat-SECRET":
        app.handle_key(ch)
    text = to_plain(views.render(app, 140, 40))
    assert "SECRET" not in text and "•" * len("sk-ant-oat-SECRET") in text
    app.handle_key("c-e")
    assert "not edited in $EDITOR" in app.current_flash()[0]
    app.handle_key("enter")
    assert ("action", "claude", "paste-token", ["sk-ant-oat-SECRET"]) in data.calls
    assert "SECRET" not in (app.current_flash() or ("",))[0]


def test_multi_prompt_action_chains_answers_with_initial_values():
    app, data = _app()
    app.open_setup()
    _goto(app, "git")
    app.handle_key("enter")
    app.handle_key("enter")
    assert app.mode["text"] == "Ada" and "Your name" in app.mode["label"]
    app.handle_key("enter")
    assert "Your email" in app.mode["label"] and not app.mode["optional"]
    app.handle_key("enter")
    assert "needs some text" in app.current_flash()[0]
    for ch in "ada@example.com":
        app.handle_key(ch)
    app.handle_key("enter")
    assert ("action", "git", "identity", ["Ada", "ada@example.com"]) in data.calls


def test_terminal_action_shows_the_command_then_rechecks():
    app, data = _app()
    app.open_setup()
    _goto(app, "claude")
    app.handle_key("enter")
    app.handle_key("enter")
    assert not any(c[0] == "action" for c in data.calls)
    assert "claude setup-token" in to_plain(views.render(app, 140, 40)) and "another terminal" in app.current_flash()[0]
    app.handle_key("enter")
    assert ("action", "claude", "setup-token", []) in data.calls


def test_nav_confirm_and_finish():
    app, data = _app()
    app.open_setup()
    _goto(app, "models")
    app.handle_key("enter")
    app.handle_key("enter")
    assert app.route["name"] == "settings"
    app.handle_key("escape")
    assert app.route["name"] == "setup" and app.setup_focus == "actions"  # back where we left
    app.handle_key("escape")
    _goto(app, "docker")
    app.handle_key("enter")
    app.handle_key("enter")
    assert app.mode["kind"] == "confirm" and app.mode["text"] == "Really?"
    app.handle_key("y")
    assert ("action", "docker", "recheck", []) in data.calls
    app.setup_focus = "steps"
    app.handle_key("F")
    assert "still to do: Job runtime image" in app.current_flash()[0] and not data.finished
    data.vm = {**data.vm, "can_finish": True, "blocking": []}
    app.recheck_setup()
    assert ("F", "finish") in app.hints()
    app.handle_key("F")
    assert data.finished and app.setup_hint is False and "setup complete" in app.current_flash()[0]


def test_failed_action_flashes_the_error():
    app, data = _app()
    app.open_setup()
    step = app.selected_step()
    app.run_setup_action(step, {"id": "boom", "label": "Explode", "kind": "run"})
    assert app.current_flash() == ("Explode: it broke", "danger")


def test_doctor_screen_quick_and_deep():
    app, data = _app()
    app.handle_key("g")
    app.handle_key("d")
    assert app.route["name"] == "doctor" and ("doctor", False) in data.calls
    text = to_plain(views.render(app, 140, 30))
    assert "Doctor" in text and "1/3 ok · 1 failing · 1 warnings" in text
    assert "✗" in text and "runtime_image" in text and "g u opens Setup" in text
    assert app.current_flash()[0] == "doctor: 1 failing"
    app.handle_key("D")
    assert ("doctor", True) in data.calls and "sandbox_selftest" in to_plain(views.render(app, 140, 30))
    pending = []
    app.run_background = pending.append
    app.handle_key("r")
    assert app.doctor_running == "quick" and "re-running checks" in to_plain(views.render(app, 140, 30))
    app.handle_key("r")
    assert len(pending) == 1 and "already running" in app.current_flash()[0]


def test_palette_lists_setup_doctor_and_every_setup_action():
    app, _ = _app()
    labels = [i["label"] for i in app.palette_items()]
    assert any(label.startswith("Setup:") for label in labels) and any(label.startswith("Doctor:") for label in labels)
    app.open_setup()
    groups = {(i["group"], i["label"]) for i in app.palette_items()}
    assert ("setup › Job runtime image", "Build the runtime image") in groups


# ---------------------------------------------------------------- /job setup, /job doctor


class UI:
    def __init__(self):
        self.info, self.errors = [], []

    def show_info(self, text):
        self.info.append(text)

    def show_error(self, text):
        self.errors.append(text)


@pytest.fixture
def no_docker(monkeypatch):
    """Every setup check is canned: docker/git/runtime ok, sandbox not run, optional steps optional."""
    states = {"sandbox": jobs_setup.StepStatus("todo", "not run yet"),
              "dind": jobs_setup.StepStatus("optional", "Sysbox is not installed"),
              "gpu": jobs_setup.StepStatus("optional", "no GPU"),
              "jira": jobs_setup.StepStatus("optional", "not configured")}

    def check(step):
        if step == "agents":
            return jobs_setup._agents_state()
        return states.get(step, jobs_setup.StepStatus("ok", f"{step} fine"))
    monkeypatch.setattr(jobs_setup, "check", check)
    monkeypatch.setattr(jobs_setup, "actions", _no_slow_actions(jobs_setup.actions))
    return states


def _no_slow_actions(real):
    def actions(step, status=None):
        return real(step, status or jobs_setup.StepStatus("todo", "")) if step in ("agents", "sandbox", "docker") else \
            [{"id": "recheck", "label": "Check again", "kind": "run"}]
    return actions


def test_job_setup_lists_the_checklist_with_commands(no_docker):
    ui = UI()
    result = job_commands.job_cmd(SimpleNamespace(ui=ui), "setup", allow_prompt=False)
    assert result.ok
    text = result.message
    assert text.startswith("Jobs setup ") and "✓ Docker — docker fine" in text
    assert "○ Sandbox self-test — not run yet" in text
    assert "/job setup sandbox selftest" in text and "To do: Sandbox self-test" in text


def test_job_setup_runs_a_prompts_action_inline(no_docker):
    from mu.jobs import job_settings
    result = job_commands.job_cmd(SimpleNamespace(ui=UI()), "setup agents defaults plan=mucli,implement=claude,review=mucli",
                                  allow_prompt=False)
    assert result.ok and "default agents saved" in result.message
    assert job_settings.rule("harness.default.implement") == "claude"
    missing = job_commands.job_cmd(SimpleNamespace(ui=UI()), "setup agents defaults", allow_prompt=False)
    assert not missing.ok and "needs 1 values" in missing.message
    bad = job_commands.job_cmd(SimpleNamespace(ui=UI()), "setup agents defaults plan=claude", allow_prompt=False)
    assert not bad.ok and "missing: implement, review" in bad.message


def test_job_setup_unknown_action_and_blocked_finish(no_docker):
    unknown = job_commands.job_cmd(SimpleNamespace(ui=UI()), "setup docker explode", allow_prompt=False)
    assert not unknown.ok and "No action 'explode' for step 'docker'" in unknown.message
    usage = job_commands.job_cmd(SimpleNamespace(ui=UI()), "setup docker", allow_prompt=False)
    assert not usage.ok and "Usage: /job setup" in usage.message
    job_commands.job_cmd(SimpleNamespace(ui=UI()), "setup", allow_prompt=False)
    blocked = job_commands.job_cmd(SimpleNamespace(ui=UI()), "setup finish", allow_prompt=False)
    assert not blocked.ok and "still to do: Sandbox self-test" in blocked.message
    no_docker["sandbox"] = jobs_setup.StepStatus("ok", "passed")
    job_commands.job_cmd(SimpleNamespace(ui=UI()), "setup recheck", allow_prompt=False)
    done = job_commands.job_cmd(SimpleNamespace(ui=UI()), "setup finish", allow_prompt=False)
    assert done.ok and jobs_setup.read_setup().get("completed_at") and not jobs_setup.needs_setup()


def test_job_doctor_reports_and_fails_on_failures(monkeypatch):
    checks = [{"name": "docker", "status": "ok", "detail": "Docker 29"},
              {"name": "runtime_image", "status": "fail", "detail": "rebuild it"},
              {"name": "gpus", "status": "warn", "detail": "no GPU"}]
    seen = []
    monkeypatch.setattr(jobs_setup, "doctor", lambda deep=False: seen.append(deep) or list(checks))
    ui = UI()
    result = job_commands.job_cmd(SimpleNamespace(ui=ui), "doctor", allow_prompt=True)
    assert not result.ok and seen == [False]
    assert "Jobs doctor  1/3 ok · 1 failing · 1 warnings" in result.message
    assert "✗ runtime_image" in result.message and "/job setup" in result.message and ui.errors
    job_commands.job_cmd(SimpleNamespace(ui=UI()), "doctor --deep", allow_prompt=False)
    assert seen == [False, True]
    checks[1]["status"] = "ok"
    assert job_commands.job_cmd(SimpleNamespace(ui=UI()), "doctor", allow_prompt=False).ok
