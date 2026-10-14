"""Terminal surfaces for job build environments: /job environments, /job create --env,
/job env …, and the /jobs TUI header, E/K keys, session folding, checks and activity."""
from types import SimpleNamespace
from typing import List

import pytest

from mu.commands import job as job_commands
from mu.jobs.sandbox import environments as envs
from mu.ui.jobs import content, views
from mu.ui.jobs.app import JobsApp
from mu.ui.jobs.data import JobsData
from mu.ui.jobs.primitives import to_plain


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture
def service_job(home, monkeypatch):
    from test_job_worker import running_job
    service, _, row, _, _ = running_job(home)
    service.release(row.id, "worker", reason="test")
    meta = dict(row.metadata or {})
    meta.update(build_environments_source="repo", build_environments=[
        {"name": "rust-build", "source": "environment", "ref": "rust-build", "paths": ["resq-backend"], "status": "ready",
         "job_image": "mucli/job-x-rust-build-runtime:1", "image_bytes": 2 * 1024 ** 3, "home_bytes": 1024 ** 3,
         "probed_env": {"PATH": "/root/.cargo/bin", "RUSTUP_HOME": "/root/.rustup"}, "skipped_paths": [".ssh (secret)"]},
        {"name": "node-build", "source": "image", "ref": "node:22", "paths": ["resq-frontend"], "status": "ready",
         "docker": True, "job_image": "mucli/job-x-node-runtime:1"}])
    service.store.update_runtime_fields(row.id, metadata_json=meta)
    monkeypatch.setattr(job_commands, "get_default_job_service", lambda: service)
    return service, service.get(row.id)


# ---------------------------------------------------------------- /job commands


def test_create_parses_repeatable_env_specs():
    values = job_commands._parse_create("Fix it --env rust-build:resq-backend --env compose-it:.:docker", {})
    assert [(e["name"], e["paths"], e["docker"]) for e in values["execution"]["environments"]] == [
        ("rust-build", ["resq-backend"], False), ("compose-it", [], True)]
    with pytest.raises(ValueError, match="--env image:bad image"):
        job_commands._parse_create("x --env 'image:bad image'", {})


def test_job_environments_lists_sources_dind_gpu_and_kept(monkeypatch, service_job):
    service, row = service_job
    envs.save_meta(service, row.id, environments_kept=True)
    monkeypatch.setattr(envs, "list_sources", lambda include_images=True: {
        "environments": [{"name": "rust-build", "status": "running", "image": "ubuntu:24.04", "gpus": "all"}],
        "templates": [{"name": "trt", "image": "mucli/template-trt:1"}],
        "images": [{"name": f"img{i}:latest", "size": "1GB"} for i in range(20)],
        "docker_in_docker": {"available": False, "install": envs.SYSBOX_GUIDANCE},
        "gpu": {"supported": True, "reason": "1 NVIDIA GPU available"}})
    result = job_commands.job_cmd(None, "environments", allow_prompt=False)
    text = result.message
    assert result.ok and "rust-build" in text and "gpus all" in text and "template:<name>" in text
    assert "… 5 more" in text and "not installed (needs Sysbox)" in text and "Install Sysbox" in text
    assert "GPUs: available" in text and "Kept jobs (1)" in text and "2.0 GB" in text


def test_job_env_show_and_mutations(monkeypatch, service_job):
    service, row = service_job
    shown = job_commands.job_cmd(None, f"env {row.id[:10]}", allow_prompt=False)
    assert shown.ok and "set from: repo" in shown.message
    assert "rust-build  environment:rust-build  [ready]" in shown.message
    assert "paths: resq-backend" in shown.message and "probed: PATH, RUSTUP_HOME" in shown.message
    assert "skipped from /root: .ssh (secret)" in shown.message and "docker (Sysbox)" in shown.message

    assert job_commands.job_cmd(None, f"env keep {row.id}", allow_prompt=False).ok
    assert service.get(row.id).metadata["environments_kept"] is True
    assert job_commands.job_cmd(None, f"env unkeep {row.id}", allow_prompt=False).ok
    assert service.get(row.id).metadata["environments_kept"] is False

    monkeypatch.setattr(envs, "cleanup_job", lambda *a, **k: {})
    monkeypatch.setattr(envs, "prepare_in_background", lambda *a, **k: None)
    refused = job_commands.job_cmd(None, f"env refresh {row.id}", allow_prompt=False)
    assert not refused.ok and "--yes" in refused.message
    assert job_commands.job_cmd(None, f"env refresh {row.id} --yes", allow_prompt=False).ok
    assert service.get(row.id).metadata["environments_refresh_requested"] is True

    set_result = job_commands.job_cmd(None, f"env set {row.id} rust-build:svc image:node:22:paths=web", allow_prompt=False)
    assert set_result.ok and "rust-build → svc · node → web" in set_result.message
    assert [e["name"] for e in service.get(row.id).execution["environments"]] == ["rust-build", "node"]
    assert job_commands.job_cmd(None, f"env clear {row.id}", allow_prompt=False).ok
    assert "environments" not in service.get(row.id).execution
    bad = job_commands.job_cmd(None, f"env set {row.id} 'image:bad image'", allow_prompt=False)
    assert not bad.ok and "Invalid build environment" in bad.message
    assert not job_commands.job_cmd(None, "env", allow_prompt=False).ok


def test_job_env_refresh_confirms_interactively(monkeypatch, service_job):
    service, row = service_job
    monkeypatch.setattr(envs, "cleanup_job", lambda *a, **k: {})
    monkeypatch.setattr(envs, "prepare_in_background", lambda *a, **k: None)
    prompts: List[str] = []
    ui = SimpleNamespace(confirm=lambda text, default=False: prompts.append(text) or False,
                         show_info=lambda t: None, show_error=lambda t: None)
    result = job_commands.job_cmd(SimpleNamespace(ui=ui), f"env refresh {row.id}", allow_prompt=True)
    assert result.ok and prompts[0].startswith("Re-snapshot the build environments")
    assert not service.get(row.id).metadata.get("environments_refresh_requested")


# ---------------------------------------------------------------- /jobs TUI


class Recording(JobsData):
    def __init__(self, service):
        super().__init__(service)
        self.sent = []

    def dispatch(self, job_id, kind, args=None):
        self.sent.append((job_id, kind, dict(args or {})))
        return {"ok": True}


def _app(service, job_id):
    app = JobsApp(Recording(service))
    app.refresh()
    app.open_job(job_id)
    return app


def test_tui_header_shows_bindings_and_keys_refresh_and_keep(service_job):
    service, row = service_job
    app = _app(service, row.id)
    text = to_plain(views.render(app, 160, 34))
    assert "⬢ rust-build → resq-backend · node-build → resq-frontend ⬢ docker · ready" in text
    assert "E refresh · K keep" in text
    app.handle_key("K")
    assert app.data.sent[-1][1:] == ("keepEnvironments", {})
    app.handle_key("E")
    assert app.mode and app.mode["kind"] == "confirm" and app.mode["text"].startswith("Re-snapshot")
    app.mode["run"]()
    assert app.data.sent[-1][1] == "refreshEnvironments"
    labels = [i["label"] for i in app.palette_items()]
    assert "Refresh environments" in labels and "Keep environments" in labels


def test_tui_header_marks_kept_and_failed(service_job):
    service, row = service_job
    meta = dict(row.metadata)
    meta["environments_kept"] = True
    meta["build_environments"][0]["status"] = "failed"
    service.store.update_runtime_fields(row.id, metadata_json=meta)
    app = _app(service, row.id)
    line = content.environments_line(app.jobs[row.id])
    assert line.endswith("· failed · ⬢ kept")


def test_session_folds_run_in_environment_and_env_progress():
    session = {"stages": {"implement": [
        {"role": "assistant", "parts": [{"type": "tool_call", "tool_name": "run_in_environment",
                                         "tool_args": {"env": "node-build", "command": "npm test", "cwd": "web"}}]},
        {"role": "user", "parts": [{"type": "tool_result", "tool_name": "run_in_environment",
                                    "tool_result": "[node-build] npm test → exit code 0"}]}]},
        "events": [{"event_type": "build_environments_preparing", "reason": "Preparing build environments (0/2)…"},
                   {"event_type": "build_environment_progress", "reason": "Preparing build environments (1/2)…"},
                   {"event_type": "build_environment_progress", "reason": "Preparing build environments (2/2)…"},
                   {"event_type": "build_environment_ready", "payload": {"binding": "rust-build"}}]}
    items = content.session_items(session)
    tool = next(i for i in items if i["kind"] == "tool")
    rendered = to_plain([content.session_item_lines(tool, 80, expanded=False, selected=False)[0]])
    assert rendered.lstrip().startswith("› [node-build] npm test")
    prep = [i for i in items if i["kind"] == "envprep"]
    assert len(prep) == 1 and prep[0]["text"] == "Preparing build environments (2/2)…"
    assert any(i["kind"] == "chip" and i["text"] == "build environment rust-build ready" for i in items)


def test_checks_show_the_environment_per_suite():
    checks = {"runs": [{"run": 1, "passed": True, "suites": [
        {"suite": "fe", "command": "npm test", "passed": True, "environment": "node-build"},
        {"suite": "unit", "command": "pytest -q", "passed": True, "environment": ""}]}]}
    text = to_plain(content.checks_lines(checks, "review_done", 100, 0, now=0))
    assert "fe [node-build] npm test" in text and "unit pytest -q" in text


def test_activity_shows_engine_broker_and_gpus(service_job):
    service, row = service_job
    app = JobsApp(Recording(service))
    app.activity_vm = {"engine": "up", "counts": {}, "jobs": [{"id": row.id, "title": "t", "state": "implementing",
        "kind": "agent", "containers": [
            {"name": f"mucli-job-{row.id[:12]}-engine", "status": "running", "role": "engine", "image": "docker:dind", "gpus": "all"},
            {"name": f"mucli-job-{row.id[:12]}-env-node-ab12", "status": "running", "role": "broker", "binding": "node-build",
             "image": "mucli/job-x"}]}], "orphans": []}
    app.push({"name": "activity"})
    text = to_plain(views.render(app, 160, 30))
    assert "engine" in text and "gpus all" in text and "broker node-build" in text


def test_live_run_in_environment_folds_from_broker_events():
    session = {"stages": {}, "events": [
        {"event_type": "sandbox_event", "payload": {"event": "tool_call_ui", "payload": {"tool_name": "run_in_environment"}}},
        {"event_type": "environment_command_started", "payload": {"env": "node-build", "command": "npm test", "cwd": "web"}},
        {"event_type": "environment_command", "payload": {"env": "node-build", "command": "npm test", "return_code": 1,
                                                          "duration_ms": 2500}}]}
    tools = [i for i in content.session_items(session) if i["kind"] == "tool"]
    assert len(tools) == 1 and tools[0]["results"] == [{"text": "exit code 1 (2.5s)", "ok": False}]
    rendered = to_plain([content.session_item_lines(tools[0], 80, expanded=False, selected=False)[0]])
    assert "› [node-build] npm test" in rendered and "exit code 1" in rendered
