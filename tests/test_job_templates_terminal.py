"""Terminal surfaces for "run a job in a container template" (documentation/plans/job_templates.md):
/job create --template [--remember], /job jira pull --template, the jobs TUI "Run in" prompt,
unverified checks rendering, and /container template setup."""
from types import SimpleNamespace

import pytest

from mu.commands import container as container_commands
from mu.commands import job as job_commands
from mu.container.templates import ContainerTemplate, TemplateRegistry
from mu.ui.jobs import content, views
from mu.ui.jobs.app import JobsApp
from mu.ui.jobs.data import JobsData
from mu.ui.jobs.primitives import to_plain


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    from utils import config
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(config, "HISTORY_DIR", str(tmp_path / "home"))
    import mu.container.templates as templates
    monkeypatch.setattr(templates, "HISTORY_DIR", str(tmp_path / "home"))
    return tmp_path


def _template(home, name="rsq-node-build", **extra):
    registry = TemplateRegistry()
    registry.upsert(ContainerTemplate(name=name, image=f"mucli/template-{name}:1", source_container="mucli-x", **extra))
    return registry


# ---------------------------------------------------------------- /job create


def test_create_parses_template_and_remember():
    values = job_commands._parse_create("Fix it --template rsq-node-build --remember", {})
    assert values["execution"]["template"] == "rsq-node-build"
    assert values["execution"]["remember_template"] is True
    assert values["title"] == "Fix it"
    assert job_commands._parse_create("x --template=template:gpu", {})["execution"]["template"] == "gpu"
    assert job_commands._parse_create("x --template default", {})["execution"]["template"] == ""
    assert "template" not in job_commands._parse_create("x", {})["execution"]
    with pytest.raises(ValueError, match="--remember needs --template"):
        job_commands._parse_create("x --remember", {})
    with pytest.raises(ValueError, match="missing value for --template"):
        job_commands._parse_create("x --template", {})


def test_create_rejects_unknown_template_and_binds_known_one(home, monkeypatch):
    from test_job_worker import running_job
    service, _, row, _, repo = running_job(home)
    _template(home)
    monkeypatch.setattr(job_commands, "get_default_job_service", lambda: service)
    monkeypatch.setattr(job_commands, "ensure_controller_daemon", lambda: SimpleNamespace(running=True, detail=""))
    defaults = {"workspace_root": str(repo), "execution": {"provider": "ollama", "model": "m"}}
    monkeypatch.setattr(job_commands, "_resolve_target", lambda session, parts, required=True: ("", defaults, parts))
    bad = job_commands.job_cmd(None, "create Fix --template nope", allow_prompt=False)
    assert not bad.ok and "Unknown template 'nope'" in bad.message
    good = job_commands.job_cmd(None, "create Fix --template rsq-node-build", allow_prompt=False)
    assert good.ok, good.message
    job = service.get(good.data["job"]["id"])
    assert [b["source"] + ":" + b["ref"] for b in job.execution["environments"]] == ["template:rsq-node-build"]
    assert "template" not in job.execution and "remember_template" not in job.execution


def test_jira_pull_passes_template_and_remember(home, monkeypatch):
    seen = []

    def fake_import(svc, *, keys, jql, defaults, force=False):
        seen.append((list(keys), dict(defaults.get("execution") or {})))
        return {"created": [], "skipped": [], "errors": []}
    _template(home)
    monkeypatch.setattr(job_commands.commands, "import_jira", fake_import)
    monkeypatch.setattr(job_commands, "get_default_job_service", lambda: None)
    monkeypatch.setattr(job_commands, "_resolve_target",
                        lambda session, parts, required=True: ("", {"workspace_root": "/r", "execution": {"model": "m"}}, parts))
    assert job_commands.job_cmd(None, "jira pull PROJ-1 --template rsq-node-build --remember", allow_prompt=False).ok
    assert seen[-1] == (["PROJ-1"], {"model": "m", "template": "rsq-node-build", "remember_template": True})
    job_commands.job_cmd(None, "jira pull PROJ-2", allow_prompt=False)
    assert seen[-1] == (["PROJ-2"], {"model": "m"})
    job_commands.job_cmd(None, "jira pull PROJ-3 --template nope", allow_prompt=False)
    assert seen[-1] == (["PROJ-3"], {"model": "m", "template": "nope"})  # the import rejects it per ticket
    bad = job_commands.job_cmd(None, "jira pull PROJ-4 --remember", allow_prompt=False)
    assert not bad.ok and "--remember needs --template" in bad.message and len(seen) == 3


def test_help_mentions_template_flags():
    import inspect
    src = inspect.getsource(job_commands)
    assert "--template NAME [--remember] (run in a container template" in src
    assert "[--template NAME [--remember]]" in src
    assert "template setup <template>" in inspect.getsource(container_commands)


# ---------------------------------------------------------------- TUI "Run in"


SOURCES = {
    "templates": [{"name": "rsq-node-build", "description": "node 20, pnpm", "job_setup": ["npm ci"], "size": "2.1 GB",
                   "gpus": "", "fit": {"missing_egress": [], "toolchains": ["node-npm"]}},
                  {"name": "gpu-enabled-ubuntu24", "description": "CUDA", "job_setup": [], "size": "15.8 GB", "gpus": "all",
                   "fit": {"missing_egress": ["registry.npmjs.org"], "toolchains": ["node-npm"]}}],
    "environments": [{"name": "rust-build", "status": "running"}],
    "remembered": {"source": "template:rsq-node-build"},
}


def test_run_in_choices_group_and_preselect_remembered():
    data = JobsData(None, creator=lambda *a: "j", sources=lambda: SOURCES)
    info = data.run_in_choices()
    assert [c["value"] for c in info["choices"]] == ["default", "template:rsq-node-build", "template:gpu-enabled-ubuntu24",
                                                    "environment:rust-build"]
    assert info["preselect"] == "template:rsq-node-build"
    gpu = info["choices"][2]["detail"]
    assert "GPU" in gpu and "15.8 GB" in gpu and "◆ can't reach registry.npmjs.org" in gpu
    assert "setup: 1 command(s)" in info["choices"][1]["detail"]
    plain = JobsData(None, creator=lambda *a: "j", sources=lambda: {**SOURCES, "remembered": None}).run_in_choices()
    assert plain["preselect"] == "default"


def test_run_in_value_parsing():
    choices = JobsData(None, sources=lambda: SOURCES).run_in_choices()["choices"]
    assert JobsApp.run_in_value("", choices) == "default"
    assert JobsApp.run_in_value("2", choices) == "template:rsq-node-build"
    assert JobsApp.run_in_value("rust-build", choices) == "environment:rust-build"
    assert JobsApp.run_in_value("env:rust-build", choices) == "environment:rust-build"
    assert JobsApp.run_in_value("template:gpu-enabled-ubuntu24", choices) == "template:gpu-enabled-ubuntu24"
    with pytest.raises(ValueError):
        JobsApp.run_in_value("9", choices)
    with pytest.raises(ValueError):
        JobsApp.run_in_value("nope", choices)


def test_new_job_prompts_title_agents_then_run_in(monkeypatch):
    created = []

    def creator(title, agents, run_in=""):
        created.append((title, agents, run_in))
        return ""
    data = JobsData(None, creator=creator, sources=lambda: SOURCES)
    monkeypatch.setattr(data, "agents", lambda: {"defaults": {}})
    app = JobsApp(data)
    monkeypatch.setattr(app, "refresh", lambda **k: None)
    app.new_job()
    app.mode["submit"]("Fix login")
    assert app.mode["new_job"] == "agents"
    app.mode["submit"]("")
    assert app.mode["new_job"] == "run_in" and "1) Default runtime" in app.mode["label"]
    assert app.mode["text"] == "rsq-node-build"  # remembered template preselected
    app.mode["submit"](app.mode["text"])
    assert created == [("Fix login", "", "template:rsq-node-build")]
    app.new_job()
    app.mode["submit"]("Other")
    app.mode["submit"]("")
    app.mode["submit"]("default")
    assert created[-1] == ("Other", "", "")


def test_dashboard_create_maps_run_in_to_flags():
    assert job_commands._run_in_flags("template:rsq") == ["--template", "rsq"]
    assert job_commands._run_in_flags("environment:rust-build") == ["--env", "environment:rust-build"]
    assert job_commands._run_in_flags("default") == [] and job_commands._run_in_flags("") == []


# ---------------------------------------------------------------- unverified checks


UNVERIFIED_RUN = {
    "run": 1, "passed": False, "verified": False, "unrunnable": 2, "trigger": "auto", "head_sha": "abc1234567",
    "suites": [
        {"suite": "check1", "command": "npm --prefix resq-management test", "passed": False, "status": "unrunnable",
         "unrunnable": {"tool": "vitest", "cause": "comes from node_modules, which isn't installed",
                        "hint": "Add `npm --prefix resq-management ci` to the template's job setup."}},
        {"suite": "check2", "command": "npm run lint", "passed": False, "status": "unrunnable",
         "unrunnable": {"tool": "eslint", "cause": "a global eslint ran instead of the project's", "hint": "Run npm ci."}},
        {"suite": "check3", "command": "pytest", "passed": True, "status": "passed"},
    ]}


def test_checks_view_renders_unverified_with_cause_and_hint():
    lines = content.checks_lines({"runs": [UNVERIFIED_RUN]}, "review_done", 120, 0, now=0)
    text = to_plain(lines)
    assert "? 1" in text and "unverified" in text
    assert "unverified · 2 checks could not run" in text
    assert "check1 unrunnable" in text and "`vitest`: comes from node_modules, which isn't installed" in text
    assert "→ Add `npm --prefix resq-management ci` to the template's job setup." in text
    assert "`eslint`: a global eslint ran instead" in text
    caution = [seg for line in lines for seg in line if seg[1] == "?"]
    assert caution and all(style.startswith("caution") for style, _ in caution)
    assert content.run_status(UNVERIFIED_RUN) == "unverified"
    assert content.run_status({"passed": True}) == "done"
    assert content.suite_status(UNVERIFIED_RUN["suites"][2]) == "done"


# ---------------------------------------------------------------- /container template setup


def test_container_template_setup_list_add_remove_clear(home):
    registry = _template(home, egress_allow=["registry.npmjs.org"])
    session = SimpleNamespace(_container_supervisor=SimpleNamespace(template_registry=registry))
    listed = container_commands.container_cmd(session, "template setup rsq-node-build", allow_prompt=False)
    assert listed.ok and "(none)" in listed.message and "registry.npmjs.org" in listed.message
    added = container_commands.container_cmd(session, "template setup rsq-node-build add npm --prefix a ci && echo 'ok'",
                                             allow_prompt=False)
    assert added.ok and registry.get("rsq-node-build").job_setup == ["npm --prefix a ci && echo 'ok'"]
    container_commands.template_cmd(session, "setup rsq-node-build add cargo fetch", allow_prompt=False)
    listed = container_commands.container_cmd(session, "template setup rsq-node-build list", allow_prompt=False)
    assert "1. npm --prefix a ci && echo 'ok'" in listed.message and "2. cargo fetch" in listed.message
    assert container_commands.container_cmd(session, "template setup rsq-node-build remove 1", allow_prompt=False).ok
    assert registry.get("rsq-node-build").job_setup == ["cargo fetch"]
    bad = container_commands.container_cmd(session, "template setup rsq-node-build remove 7", allow_prompt=False)
    assert not bad.ok and "1–1" in bad.message
    too_long = container_commands.container_cmd(session, "template setup rsq-node-build add " + "x" * 2001,
                                                allow_prompt=False)
    assert not too_long.ok and "2000" in too_long.message
    assert container_commands.container_cmd(session, "template setup rsq-node-build clear", allow_prompt=False).ok
    assert registry.get("rsq-node-build").job_setup == []
    missing = container_commands.container_cmd(session, "template setup ghost", allow_prompt=False)
    assert not missing.ok and "Template not found: ghost" in missing.message
    assert not container_commands.container_cmd(session, "template setup", allow_prompt=False).ok
