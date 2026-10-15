"""Web surfaces for job build environments (documentation/plans/job_build_environments.md §10):
New job field + editor, job page pill/panel with E/K, session folding, checks column, Activity
roles, Settings status/kept jobs and the Environments page badge."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "mu" / "gui" / "static"
HTML = (ROOT / "mu" / "gui" / "templates" / "work.html").read_text(encoding="utf-8")
ENV = (STATIC / "js" / "work" / "environments.js").read_text(encoding="utf-8")
VIEW = (STATIC / "js" / "work" / "view.js").read_text(encoding="utf-8")
SHELL = (STATIC / "js" / "work.js").read_text(encoding="utf-8")
RULES = (STATIC / "js" / "work" / "rules.js").read_text(encoding="utf-8")
ACTIVITY = (STATIC / "js" / "work" / "activity.js").read_text(encoding="utf-8")
CONTAINERS = (STATIC / "js" / "containers.js").read_text(encoding="utf-8")
CSS = (STATIC / "css" / "work_polish.css").read_text(encoding="utf-8")
# Wash W4: the Environments page styles live in its pattern bundle (legacy containers*.css retired).
CONTAINERS_CSS = (STATIC / "css" / "wash" / "patterns" / "containers.css").read_text(encoding="utf-8")


def test_environments_module_loads_before_the_views():
    assert HTML.index("/static/js/work/environments.js") < HTML.index("/static/js/work/view.js") < HTML.index("/static/js/work.js")
    assert "window.MuJobEnvironments" in ENV and "`/api/jobs/environments?${q}`" in ENV


def test_new_job_sheet_shows_the_resolved_set_and_sends_the_override():
    for el in ('id="job-environments-field"', 'id="job-environments-summary"', 'id="job-environments-change"', 'id="job-environments-reset"'):
        assert el in HTML
    # Workspace-first sheet: the preview follows the chosen workspace and the session that uses it.
    assert "refreshEnvPreview" in SHELL and "workspaceInput.addEventListener('input'" in SHELL
    assert "repository: path, session: sessionForWorkspace(path)?.name || ''" in SHELL
    assert "execution.environments = envOverride" in SHELL
    # Source labels for repo config / session / settings / default runtime.
    for source in ("repo:'repo config'", "session:'session'", "settings:'settings'", "default:'default runtime'", "job:'this job'"):
        assert source in ENV


def test_editor_rows_pick_a_source_and_set_paths_docker_primary_gpus():
    assert "<datalist" in ENV and "info?.environments" in ENV and "info?.templates" in ENV and "info?.images" in ENV
    for key in ('data-env-key="env"', 'data-env-key="paths"', 'data-env-key="docker"', 'data-env-key="primary"', 'data-env-key="gpus"'):
        assert key in ENV
    assert "data-env-add" in ENV and "data-env-remove" in ENV
    assert "Docker-in-Docker:" in ENV and "Two rows are both named" in ENV
    # Blank GPUs = inherit (omitted from the binding).
    assert "if (String(row.gpus || '').trim()) out.gpus" in ENV


def test_job_page_pill_panel_and_commands():
    assert "ENV.headerPill(job.environments || job.build_environments, envOpen)" in VIEW
    assert "'shared runtime'" in ENV and "· kept" in ENV and "s.status === 'failed' ? 'danger'" in ENV
    assert "data-environments" in VIEW and "ENV.panel(job.build_environments, buttons)" in VIEW
    for part in ("probed_env", "skipped_paths", "secret_paths", "image_bytes", "x.engine"):
        assert part in ENV
    # Commands go through invoke(): confirm text, setEnvironments dialog, E/K shortcuts in the palette.
    assert "dialogOf(action) === 'environments'" in VIEW and "type:'setEnvironments'" in VIEW
    assert "['refreshEnvironments', 'keepEnvironments', 'setEnvironments']" in VIEW
    assert "!hiddenOf(a) || ENV_ACTIONS.has(a.id)" in VIEW


def test_session_folds_run_in_environment_and_shows_preparation():
    assert "e.event_type === 'environment_command'" in VIEW and "`[${p.env || '?'}] ${p.command || ''}" in VIEW
    assert "inner.tool_name === 'run_in_environment') continue" in VIEW
    assert "e.event_type === 'environment_command_started'" in VIEW and "pendingEnv" in VIEW
    assert "ENV?.toolSummary(p.tool_name, p.tool_args)" in VIEW and "name !== 'run_in_environment'" in ENV
    assert "'build_environment_progress'" in VIEW and "Preparing build environments…" in VIEW


def test_checks_tab_has_a_per_suite_environment_column():
    assert '<th scope="col">Environment</th>' in VIEW and "data-suite-env" in VIEW and "shared runtime" in VIEW


def test_activity_shows_engine_broker_binding_and_gpus():
    assert "c.role === 'engine'" in ACTIVITY and "c.role === 'broker'" in ACTIVITY
    assert "c.binding" in ACTIVITY and "gpus ${esc(c.gpus)}" in ACTIVITY


def test_settings_show_docker_in_docker_status_and_kept_jobs():
    assert "section.id === 'build_environments'" in RULES and "data-env-status" in RULES
    assert "'/api/jobs/environments?images=false'" in RULES and "dind.install" in RULES
    assert "type: 'keepEnvironments'" in RULES and "keep: false" in RULES and "data-unkeep" in RULES


def test_environments_page_badge_and_scan_warning():
    assert "ref.jobs_using" in CONTAINERS and "Used by ${Number(ref.jobs_using)} job" in CONTAINERS
    assert "ref.job_scan_blocks" in CONTAINERS and "Secret scan blocked job" in CONTAINERS
    assert "w-env-jobs-badge" in CONTAINERS and "w-env-scan-warning" in CONTAINERS
    assert ".w-env-jobs-badge" in CONTAINERS_CSS and ".w-env-scan-warning" in CONTAINERS_CSS
    assert ".work-env-pill" in CSS and ".work-env-binding" in CSS


def test_environments_route_serves_sources_and_preview(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    from mu.gui.routers import jobs as router
    from mu.jobs.sandbox import environments as envs
    monkeypatch.setattr(envs, "list_sources", lambda include_images=True, docker=None: {
        "environments": [], "templates": [], "images": [], "docker_in_docker": {"available": False}, "gpu": {"supported": False}})
    repo = tmp_path / "repo"
    (repo / ".mucli").mkdir(parents=True)
    (repo / ".mucli" / "jobs.toml").write_text('[[environments]]\nname = "rust-build"\npaths = ["svc"]\n')
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_service=None)))
    data = router.list_build_environments(request, repository=str(repo), images=False)
    if asyncio.iscoroutine(data):
        data = asyncio.run(data)
    assert data["resolved"]["source"] == "repo" and data["resolved"]["summary"] == "rust-build → svc"
    assert data["docker_in_docker"] == {"available": False}
