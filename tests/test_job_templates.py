"""Run a job in a container template (documentation/plans/job_templates.md): template job fields,
the "Run in" payload, remember, fit, the PATCH endpoint, Jira import, template job setup and its
isolation, and the honest gate (unrunnable checks, unverified runs, review cap, push override)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.container import templates as templates_mod
from mu.container.templates import ContainerTemplate, TemplateRegistry, clean_egress_allow, clean_job_setup
from mu.jobs import JobService, JobStore
from mu.jobs.sandbox import environments as envs
from test_job_worker import git, running_job

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("MUCLI_HOME", str(root))
    import utils.config as config
    monkeypatch.setattr(config, "HISTORY_DIR", str(root))
    monkeypatch.setattr(templates_mod, "HISTORY_DIR", str(root))
    from mu.jobs.sandbox import cli
    monkeypatch.setattr(cli, "selinux_enforcing", lambda *a, **k: False)
    monkeypatch.setattr(cli, "is_podman", lambda *a, **k: False)
    return tmp_path


def add_template(name="rsq-node-build", **fields):
    return TemplateRegistry().upsert(ContainerTemplate(name=name, image=f"mucli/template-{name}:1",
                                                       source_container="mucli-src", **fields))


def node_repo(tmp_path, *, lock=True):
    repo = tmp_path / "workspace"
    repo.mkdir()
    for args in (("init", "-b", "main"), ("config", "user.name", "T"), ("config", "user.email", "t@e.com")):
        git(repo, *args)
    (repo / "package.json").write_text('{"name": "x", "scripts": {"test": "vitest run"}}\n')
    if lock:
        (repo / "package-lock.json").write_text("{}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo


EXE = {"provider": "openai", "model": "gpt", "session_type": "workspace"}


# ------------------------------------------------------------------ template fields (§4)


def test_registry_reads_old_entries_and_round_trips_job_fields(home):
    registry = TemplateRegistry()
    Path(registry.registry_path).write_text(json.dumps({"old": {
        "name": "old", "image": "mucli/template-old:1", "source_container": "mucli-old", "egress_allow": ["pypi.org"],
        "unknown_future_field": 1}}))
    old = registry.get("old")
    assert old.job_setup == [] and old.egress_allow == ["pypi.org"]
    updated = registry.update("old", job_setup=["npm ci", "  ", "cargo fetch"], egress_allow=["Registry.NPMJS.org."],
                              description=" node ")
    assert updated.job_setup == ["npm ci", "cargo fetch"] and updated.egress_allow == ["registry.npmjs.org"]
    assert registry.get("old").description == "node" and registry.get("old").job_setup == ["npm ci", "cargo fetch"]
    assert registry.update("missing", job_setup=[]) is None


def test_job_setup_and_egress_validation():
    assert clean_job_setup(["a"] * 20) == ["a"] * 20
    with pytest.raises(ValueError, match="at most 20"):
        clean_job_setup(["a"] * 21)
    with pytest.raises(ValueError, match="2000 characters"):
        clean_job_setup(["x" * 2001])
    with pytest.raises(ValueError):
        clean_job_setup("npm ci")
    with pytest.raises(ValueError):
        clean_job_setup([1])
    assert clean_egress_allow(["*.npmjs.org", "registry.npmjs.org", "registry.npmjs.org"]) == ["*.npmjs.org", "registry.npmjs.org"]
    for bad in ("10.0.0.1", "*.com", "*.co.uk", "bad domain", "-x.org"):
        with pytest.raises(ValueError, match="invalid egress domain"):
            clean_egress_allow([bad])


def test_resnapshot_keeps_job_setup_and_added_egress(home, monkeypatch):
    from mu.container import supervisor as sup
    add_template("rsq", job_setup=["npm ci"], egress_allow=["registry.npmjs.org"])
    calls = []
    monkeypatch.setattr("mu.jobs.sandbox.environments.build_home_snapshot", lambda *a, **k: calls.append(a) or {})
    runner = SimpleNamespace(require=lambda name: "/usr/bin/docker", run=lambda *a, **k: SimpleNamespace(returncode=0),
                             commands=[])
    supervisor = SimpleNamespace(template_registry=TemplateRegistry(), runner=runner,
                                 _container_state=lambda name: (True, False),
                                 resolve=lambda name: SimpleNamespace(name="mucli-src", root_volume="", container_volume="/w",
                                                                      dockerfile_hash="h", egress_allow=["pypi.org"],
                                                                      egress_deny=[]))
    template = sup.ContainerSupervisor.snapshot(supervisor, "mucli-src", "rsq")
    assert template.job_setup == ["npm ci"] and template.egress_allow == ["pypi.org", "registry.npmjs.org"]


# ------------------------------------------------------------------ PATCH /api/containers/templates/{name}


def _containers_client(service=None):
    from mu.gui.routers import containers
    app = FastAPI()
    app.state.container_supervisor = SimpleNamespace(template_registry=TemplateRegistry())
    app.state.job_service = service
    app.include_router(containers.router)
    return TestClient(app)


def test_patch_template_validates_and_returns_the_template(home):
    add_template("rsq")
    with _containers_client() as client:
        ok = client.patch("/api/containers/templates/rsq",
                          json={"job_setup": ["npm --prefix resq-management ci"], "egress_allow": ["registry.npmjs.org"],
                                "description": "Node"})
        assert ok.status_code == 200
        body = ok.json()["template"]
        assert body["job_setup"] == ["npm --prefix resq-management ci"] and body["egress_allow"] == ["registry.npmjs.org"]
        assert body["description"] == "Node" and body["jobs_using"] == 0
        assert client.patch("/api/containers/templates/rsq", json={"egress_allow": ["1.2.3.4"]}).status_code == 400
        assert client.patch("/api/containers/templates/rsq", json={"job_setup": ["x"] * 21}).status_code == 400
        assert client.patch("/api/containers/templates/rsq", json={"job_setup": ["x" * 2001]}).status_code == 400
        assert client.patch("/api/containers/templates/rsq", json={"image": "evil"}).status_code == 400
        assert client.patch("/api/containers/templates/nope", json={"job_setup": []}).status_code == 404
        # Unchanged by the rejected edits; partial updates keep the other fields.
        partial = client.patch("/api/containers/templates/rsq", json={"description": "Node 22"}).json()["template"]
        assert partial["job_setup"] == ["npm --prefix resq-management ci"] and partial["egress_allow"] == ["registry.npmjs.org"]


def test_templates_list_includes_job_fields_and_jobs_using(home, tmp_path):
    add_template("rsq", job_setup=["npm ci"])
    repo = node_repo(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    service.create_from_payload({"title": "t", "workspace_root": str(repo), "execution": {**EXE, "template": "rsq"}})
    from mu.gui.routers import containers
    supervisor = SimpleNamespace(template_registry=TemplateRegistry(), list_environments=lambda: [])
    app = FastAPI()
    app.state.container_supervisor = supervisor
    app.state.job_service = service
    app.include_router(containers.router)
    with TestClient(app) as client:
        rows = client.get("/api/containers").json()["templates"]
    assert rows[0]["job_setup"] == ["npm ci"] and rows[0]["jobs_using"] == 1


# ------------------------------------------------------------------ sources: size, fit, remembered (§3.1)


class FakeDocker:
    def __init__(self, sizes=None):
        self.sizes = sizes or {}
        self.runner = SimpleNamespace(commands=[], require=lambda name: "/usr/bin/docker")

    def run(self, *args, check=True, timeout=None, input_text=None):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def inspect(self, kind, ref, fmt):
        return str(self.sizes.get(ref, ""))

    def exists(self, kind, ref):
        return True


def test_list_sources_templates_carry_size_setup_egress(home, monkeypatch):
    add_template("rsq", job_setup=["npm ci", "npm --prefix a ci"], egress_allow=["registry.npmjs.org"])
    monkeypatch.setattr(envs, "sysbox_available", lambda docker=None: False)
    data = envs.list_sources(docker=FakeDocker({"mucli/template-rsq:1": 2_100_000_000}), include_images=False)
    row = data["templates"][0]
    assert row["size"] == "2.1 GB" and row["size_bytes"] == 2_100_000_000 and row["gpus"] == ""
    assert row["job_setup"] == ["npm ci", "npm --prefix a ci"] and row["egress_allow"] == ["registry.npmjs.org"]
    assert row["spec"] == "template:rsq"


def test_fit_warns_for_unreachable_registries_and_allow_clears_it(home, tmp_path, monkeypatch):
    from mu.jobs.core.vm import environments_vm
    add_template("gpu-enabled-ubuntu24", egress_allow=["pypi.org", "api.anthropic.com"])
    add_template("rsq", egress_allow=["*.npmjs.org"])
    repo = node_repo(tmp_path)
    monkeypatch.setattr(envs, "Docker", lambda *a, **k: FakeDocker())
    monkeypatch.setattr(envs, "sysbox_available", lambda docker=None: False)
    data = environments_vm(repository=str(repo), include_images=False)
    fit = {t["name"]: t["fit"] for t in data["templates"]}
    assert fit["gpu-enabled-ubuntu24"] == {"missing_egress": ["registry.npmjs.org"], "toolchains": ["node-npm"]}
    assert fit["rsq"]["missing_egress"] == []
    assert data["remembered"] is None
    # Allow = PATCH the template's egress with the missing domain.
    with _containers_client() as client:
        allowed = client.patch("/api/containers/templates/gpu-enabled-ubuntu24",
                               json={"egress_allow": ["pypi.org", "api.anthropic.com", "registry.npmjs.org"]})
        assert allowed.status_code == 200
    again = environments_vm(repository=str(repo), include_images=False)
    assert {t["name"]: t["fit"] for t in again["templates"]}["gpu-enabled-ubuntu24"]["missing_egress"] == []
    # Without a repository there is no fit and nothing remembered.
    bare = environments_vm(include_images=False)
    assert "fit" not in bare["templates"][0] and bare["remembered"] is None


# ------------------------------------------------------------------ job create payload (§3.1)


def test_execution_template_normalises_rejects_unknown_and_blank_means_no_change(home, tmp_path):
    add_template("rsq")
    repo = node_repo(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "t", "workspace_root": str(repo), "execution": {**EXE, "template": "rsq"}})
    assert [b["source"] + ":" + b["ref"] for b in job.execution["environments"]] == ["template:rsq"]
    assert "template" not in job.execution and "remember_template" not in job.execution
    with pytest.raises(ValueError, match="Unknown template 'nope'"):
        service.create_from_payload({"title": "t", "workspace_root": str(repo), "execution": {**EXE, "template": "nope"}})
    plain = service.create_from_payload({"title": "t", "workspace_root": str(repo), "execution": {**EXE, "template": ""}})
    assert "environments" not in plain.execution and "template" not in plain.execution
    advanced = service.create_from_payload({"title": "t", "workspace_root": str(repo),
                                            "execution": {**EXE, "environments": ["image:node:22"]}})
    assert advanced.execution["environments"][0]["ref"] == "node:22"


def test_remember_writes_the_toolchains_row_and_drives_preselection(home, tmp_path, monkeypatch):
    from mu.jobs.core.vm import environments_vm
    from mu.jobs.job_settings import load_settings, rule, set_rule
    add_template("rsq")
    repo = node_repo(tmp_path)
    set_rule("sandbox.toolchains", [{"repository": "/elsewhere", "base_image": "rust:1"},
                                    {"repository": str(repo), "base_image": "node:20", "paths": ""}])
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "t", "workspace_root": str(repo),
                                       "execution": {**EXE, "template": "rsq", "remember_template": True}})
    rows = rule("sandbox.toolchains", load_settings())
    assert {"repository": "/elsewhere", "base_image": "rust:1"} in rows
    mine = [r for r in rows if r["repository"] == os.path.realpath(repo)]
    assert mine == [{"repository": os.path.realpath(repo), "base_image": "template:rsq", "paths": "", "docker": "", "gpus": ""}]
    assert "remember_template" not in job.execution
    assert any(e.event_type == "template_remembered" for e in service.store.recent_events(job.id))
    monkeypatch.setattr(envs, "Docker", lambda *a, **k: FakeDocker())
    monkeypatch.setattr(envs, "sysbox_available", lambda docker=None: False)
    data = environments_vm(repository=str(repo), include_images=False)
    assert data["remembered"] == {"source": "template:rsq"}
    # Repository-level default: a job created without a choice resolves to the template (scheduled imports too).
    later = service.create_from_payload({"title": "t", "workspace_root": str(repo), "execution": dict(EXE)})
    bindings, origin = envs.resolve_bindings(later)
    assert origin == "settings" and [(b.source, b.ref) for b in bindings] == [("template", "rsq")]


def test_remember_keeps_per_component_toolchain_rows(home, tmp_path):
    """Remembering replaces only the whole-repository row; monorepo component rows survive."""
    from mu.jobs.job_settings import load_settings, rule, set_rule
    add_template("rsq")
    repo = node_repo(tmp_path)
    component = {"repository": str(repo), "base_image": "rust:1", "paths": "engine"}
    set_rule("sandbox.toolchains", [component, {"repository": str(repo), "base_image": "node:20", "paths": ""}])
    envs.remember_template(str(repo), "rsq")
    rows = rule("sandbox.toolchains", load_settings())
    assert component in rows
    whole = [r for r in rows if r["repository"] == os.path.realpath(repo) and not r.get("paths")]
    assert [r["base_image"] for r in whole] == ["template:rsq"]


def test_create_endpoint_400_for_unknown_template(home, tmp_path):
    from mu.gui.routers import jobs as jobs_router
    repo = node_repo(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    app = FastAPI()
    app.state.job_service = service
    app.include_router(jobs_router.router, prefix="/api/jobs")
    with TestClient(app) as client:
        response = client.post("/api/jobs", json={"title": "t", "workspace_root": str(repo),
                                                  "execution": {**EXE, "template": "ghost"}})
    assert response.status_code == 400 and response.json()["detail"] == "Unknown template 'ghost'"


# ------------------------------------------------------------------ Jira import (manual and scheduled)


class FakeTracker:
    name = "jira"

    def __init__(self, keys):
        self.keys = keys

    def fetch(self, key):
        return {"key": key}

    def search(self, query, max_results=20):
        return [{"key": k} for k in self.keys]

    def key_of(self, issue):
        return issue["key"]

    def to_payload(self, issue):
        return {"title": f"Ticket {issue['key']}", "metadata": {"ticket": {"key": issue["key"]}}}


def test_jira_import_defaults_template_binds_every_job(home, tmp_path, monkeypatch):
    from mu.jobs import tracker_sync
    add_template("rsq")
    repo = node_repo(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    monkeypatch.setattr(tracker_sync, "notify", lambda *a, **k: None)
    result = tracker_sync.import_tickets(service, FakeTracker(["A-1", "A-2"]), keys=["A-1", "A-2"], defaults={
        "workspace_root": str(repo), "execution": {**EXE, "template": "rsq", "remember_template": True}})
    assert len(result["created"]) == 2 and not result["errors"]
    for created in result["created"]:
        job = service.get(created["job_id"])
        assert [(b["source"], b["ref"]) for b in job.execution["environments"]] == [("template", "rsq")]
    assert envs.remembered_template(str(repo)) == {"source": "template:rsq"}


def test_jira_import_endpoint_rejects_unknown_template(home, tmp_path):
    from mu.gui.routers import jobs as jobs_router
    repo = node_repo(tmp_path)
    app = FastAPI()
    app.state.job_service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    app.include_router(jobs_router.router, prefix="/api/jobs")
    with TestClient(app) as client:
        response = client.post("/api/jobs/jira/import", json={"keys": ["A-1"], "defaults": {
            "workspace_root": str(repo), "execution": {"template": "ghost"}}})
    assert response.status_code == 400 and response.json()["detail"] == "Unknown template 'ghost'"


def test_scheduled_sync_entries_carry_template(home, tmp_path, monkeypatch):
    from mu.integrations import jira_settings
    from mu.jobs import tracker_sync
    cleaned = jira_settings._clean_watch({"enabled": True, "queries": [
        {"tracker": "jira", "query": "project = A", "workspace_root": "/w", "template": "template:rsq"},
        {"tracker": "jira", "query": "project = B", "workspace_root": "/w", "template": "bad name!"}]})
    assert [q["template"] for q in cleaned["queries"]] == ["rsq", ""]
    add_template("rsq")
    repo = node_repo(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    monkeypatch.setattr(jira_settings, "watch_settings", lambda: {"enabled": True, "interval_seconds": 60, "queries": [
        {"tracker": "jira", "query": "project = A", "workspace_root": str(repo), "session_name": "", "max_results": 5,
         "template": "rsq"}]})
    monkeypatch.setattr(tracker_sync, "tracker_for", lambda name: FakeTracker(["A-9"]))
    monkeypatch.setattr(tracker_sync, "notify", lambda *a, **k: None)
    outcome = tracker_sync.poll_watches(service, now=10_000)
    assert outcome["ran"][0]["created"] == ["A-9"]
    job = service.list()[0]
    assert [(b["source"], b["ref"]) for b in job.execution["environments"]] == [("template", "rsq")]


# ------------------------------------------------------------------ job setup (§4)


class SetupRunner:
    """Fake docker CLI for setup containers: ``create`` records args, ``start`` writes the result."""

    def __init__(self, codes=None, output="added 120 packages"):
        self.codes = list(codes or [])
        self.output = output
        self.creates = []
        self.commands = []
        self.dirs = {}

    def require(self, name):
        return "/usr/bin/docker"

    def run(self, args, check=True, timeout=None, input_text=None, output_callback=None):
        args = list(args)
        if args[1] == "create":
            name = args[args.index("--name") + 1]
            out = next(a.split("src=")[1].split(",")[0] for a in args if a.startswith("type=bind") and "dst=/output" in a)
            inp = next(a.split("src=")[1].split(",")[0] for a in args if a.startswith("type=bind") and "dst=/artifacts" in a)
            self.dirs[name] = (Path(inp), Path(out))
            self.creates.append(args)
        elif args[1] == "start":
            name = args[-1]
            inp, out = self.dirs[name]
            request = json.loads((inp / "request.json").read_text())
            self.commands.append(request)
            code = self.codes.pop(0) if self.codes else 0
            if output_callback:
                output_callback("stdout", json.dumps({"event": "check_output", "stream": "stdout", "text": self.output}))
            (out / "result.json").write_text(json.dumps({"passed": code == 0, "return_code": code, "timed_out": False,
                                                         "stdout": self.output, "stderr": "" if code == 0 else
                                                         "npm ERR! code E403\nnpm ERR! 403 Forbidden"}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _setup_job(tmp_path):
    service, _, job, _, repo = running_job(tmp_path)
    state = {"name": "rsq", "source": "template", "ref": "rsq", "paths": [], "status": "ready",
             "job_image": "mucli/job-x-rsq-runtime:1", "python": "/opt/mucli-venv/bin/python",
             "egress_allow": ["registry.npmjs.org", "api.openai.com", "*.anthropic.com"],
             "job_setup": ["npm --prefix resq-management ci", "npm --prefix resq-frontend ci"]}
    return service, service.get(job.id), state


def test_setup_runs_commands_in_order_with_stage_isolation(home, tmp_path, monkeypatch):
    from mu.jobs.sandbox import egress, job_setup
    bridges = []

    class Bridge:
        def __init__(self, directory, domains):
            bridges.append(list(domains))

        def start(self):
            return self

        def close(self):
            pass
    monkeypatch.setattr(egress, "EgressBridge", Bridge)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-leak")
    service, job, state = _setup_job(tmp_path)
    runner = SetupRunner()
    job_setup.run_job_setup(service, job, [state], runner=runner, settings={})
    assert [c["command"] for c in runner.commands] == state["job_setup"]
    assert all(c["shell"] is True and c["cwd"] == "" for c in runner.commands)
    for args in runner.creates:
        text = " ".join(args)
        assert "--network none" in text and "--cap-drop ALL" in text and "--read-only" in text
        assert "no-new-privileges:true" in text and f"--user {os.getuid()}:{os.getgid()}" in text
        assert "io.mucli.role=job-setup" in text and "--entrypoint /opt/mucli-venv/bin/python" in text
        assert args[-3:] == ["mucli/job-x-rsq-runtime:1", "-m", "mu.jobs.sandbox.check_worker"]
        ws = next(a for a in args if "dst=/workspace," in a + "," and a.startswith("type=bind") and a.endswith("dst=/workspace"))
        assert "readonly" not in ws  # worktree read-write, as for implement
        assert any(a.startswith("type=bind") and a.endswith("dst=/cache") for a in args)
        assert "sk-should-never-leak" not in text and "ANTHROPIC" not in text and "--gpus" not in text
        assert "MUCLI_STAGE_TOKEN=" in text  # empty token: no model relay access
    # Egress: the template's allowlist with model hosts dropped.
    assert bridges and all(domains == ["registry.npmjs.org"] for domains in bridges)
    record = job_setup.setup_records(service.get(job.id))["rsq"]
    assert record["status"] == "ok" and record["image"] == state["job_image"]
    events = [e for e in service.store.recent_events(job.id) if e.event_type == "environment_setup"]
    assert [(e.payload["index"], e.payload["status"]) for e in events] == [(0, "running"), (0, "ok"), (1, "running"), (1, "ok")]
    assert events[1].payload["exit_code"] == 0 and "added 120 packages" in events[1].payload["output_tail"]
    assert events[0].payload == {"binding": "rsq", "command": state["job_setup"][0], "index": 0, "status": "running"}
    # Once per job image: nothing re-runs for the same image.
    job_setup.run_job_setup(service, service.get(job.id), [state], runner=runner, settings={})
    assert len(runner.commands) == 2


def test_setup_failure_parks_with_command_and_log_tail_then_skip(home, tmp_path, monkeypatch):
    from mu.jobs.core.actions import action_map
    from mu.jobs.core.commands import dispatch
    from mu.jobs.core.vm import facts_for, job_vm
    from mu.jobs.models import AttentionReason
    from mu.jobs.sandbox import job_setup
    from mu.jobs.sandbox.environments import EnvironmentBlocked
    monkeypatch.setattr("mu.jobs.sandbox.egress.EgressBridge", lambda d, domains: SimpleNamespace(start=lambda: SimpleNamespace(close=lambda: None)))
    service, job, state = _setup_job(tmp_path)
    runner = SetupRunner(codes=[0, 1])
    with pytest.raises(EnvironmentBlocked) as raised:
        job_setup.run_job_setup(service, job, [state], runner=runner, settings={})
    message = str(raised.value)
    assert "`npm --prefix resq-frontend ci` exited 1" in message and "npm ERR! code E403" in message
    assert raised.value.setup_failure["index"] == 1
    # The stage sites park on EnvironmentBlocked with ENVIRONMENT_FAILURE and the message as detail.
    service.require_human(job.id, AttentionReason.ENVIRONMENT_FAILURE, message[:2000], payload={"stage": "plan"})
    parked = service.get(job.id)
    assert parked.attention_reason.value == "environment_failure" and "resq-frontend ci" in parked.attention_detail
    actions = action_map(facts_for(service, parked))
    assert actions["skipSetup"].enabled and actions["skipSetup"].label == "Continue without setup"
    assert actions["continueUnverified"].enabled is False
    vm_actions = {a["id"]: a for a in job_vm(service, job.id)["actions"]}
    assert vm_actions["skipSetup"]["enabled"] and vm_actions["skipSetup"]["confirm"]
    service.release(job.id, "worker", reason="test")
    dispatch(service, {"type": "skipSetup", "job_id": job.id})
    assert job_setup.setup_records(service.get(job.id))["rsq"]["status"] == "skipped"
    assert not job_setup.needs_setup(service.get(job.id), state)


def test_ensure_environments_runs_setup_and_reruns_after_refresh(home, tmp_path, monkeypatch):
    monkeypatch.setattr("mu.jobs.sandbox.egress.EgressBridge", lambda d, domains: SimpleNamespace(start=lambda: SimpleNamespace(close=lambda: None)))
    add_template("rsq", job_setup=["npm ci"], egress_allow=["registry.npmjs.org"])
    service, _, row, _, repo = running_job(tmp_path)
    service.store.update_runtime_fields(row.id, execution_json={**row.execution, "environments": ["template:rsq"]})
    images = iter(["mucli/job-x-rsq-runtime:1", "mucli/job-x-rsq-runtime:2"])

    def fake_snapshot(job, binding, *, docker, settings, log):
        source = envs.resolve_source(binding)
        return {**binding, "status": "ready", "job_image": next(images), "egress_allow": source["egress_allow"],
                "job_setup": source["job_setup"], "gpus": "", "python": "python3"}
    monkeypatch.setattr(envs, "snapshot_binding", fake_snapshot)
    runner = SetupRunner()
    docker = FakeDocker()
    docker.runner = runner
    states = envs.ensure_environments(service, service.get(row.id), docker=docker, settings={})
    assert states[0]["job_setup"] == ["npm ci"] and [c["command"] for c in runner.commands] == ["npm ci"]
    envs.ensure_environments(service, service.get(row.id), docker=docker, settings={})
    assert len(runner.commands) == 1  # same image: no re-run
    monkeypatch.setattr(envs, "cleanup_job", lambda *a, **k: {})
    envs.request_refresh(service, row.id)
    envs.ensure_environments(service, service.get(row.id), docker=docker, settings={})
    assert len(runner.commands) == 2  # refreshed image: setup again
    described = envs.describe(service.get(row.id))["bindings"][0]
    assert described["setup"]["status"] == "ok" and described["job_setup"] == ["npm ci"]
    assert described["setup"]["commands"][0]["status"] == "ok"


def test_setup_output_is_excluded_from_dirty_worktree_detection(home, tmp_path, monkeypatch):
    from mu.jobs.checks import ChecksStage, _without_setup_files
    from mu.jobs.sandbox import job_setup
    monkeypatch.setattr("mu.jobs.sandbox.egress.EgressBridge", lambda d, domains: SimpleNamespace(start=lambda: SimpleNamespace(close=lambda: None)))
    service, job, state = _setup_job(tmp_path)

    class WritingRunner(SetupRunner):
        def run(self, args, **kwargs):
            if list(args)[1] == "start":
                Path(job.worktree, "generated.lock").write_text("x")  # not git-ignored
            return super().run(args, **kwargs)
    job_setup.run_job_setup(service, job, [{**state, "job_setup": ["npm ci"]}], runner=WritingRunner(), settings={})
    assert job_setup.setup_files(service.get(job.id)) == ["generated.lock"]
    dirty = [e for e in service.store.recent_events(job.id) if e.event_type == "environment_setup_dirty"]
    assert dirty and "generated.lock" in dirty[0].reason
    fresh = service.get(job.id)
    assert _without_setup_files(fresh, "?? generated.lock\n M src.py") == " M src.py"
    run = ChecksStage(service, run_suite=lambda j, w, s: {"passed": True, "return_code": 0, "stdout": "", "stderr": ""},
                      settings={}).verify(replace(fresh, validation_commands=["python -m pytest"]))
    assert run.summary["dirty_worktree"] is False and run.passed


# ------------------------------------------------------------------ honest gate (§5): the rsq-cloud fixtures

RSQ_PACKAGE = {"name": "resq-management", "version": "0.1.0",
               "scripts": {"test": "vitest run", "typecheck": "tsc --noEmit -p tsconfig.json",
                           "lint": 'eslint . && prettier --check "src/**/*.{ts,tsx,css}"'},
               "devDependencies": {"vitest": "^2.1.0", "typescript": "^5.6.0", "eslint": "^9.0.0", "prettier": "^3.3.0"}}
ESLINT_64 = ("\nOops! Something went wrong! :(\n\nESLint: 6.4.0.\n\nESLint couldn't find a configuration file. To set up a "
             "configuration file for this project, please run:\n\n    eslint --init\n\nESLint looked for configuration files "
             "in /workspace/resq-management and its ancestors. If it found none, it then looked in your home directory.\n")
RSQ_RESULTS = {
    "npm --prefix resq-management test": {"passed": False, "return_code": 127, "timed_out": False,
                                          "stdout": "\n> resq-management@0.1.0 test\n> vitest run\n\n",
                                          "stderr": "sh: 1: vitest: not found\n"},
    "npm --prefix resq-management run typecheck": {"passed": False, "return_code": 127, "timed_out": False,
                                                   "stdout": "\n> resq-management@0.1.0 typecheck\n> tsc --noEmit -p tsconfig.json\n\n",
                                                   "stderr": "sh: 1: tsc: not found\n"},
    "npm --prefix resq-management run lint": {"passed": False, "return_code": 2, "timed_out": False,
                                              "stdout": "\n> resq-management@0.1.0 lint\n> eslint . && prettier --check \"src/**/*.{ts,tsx,css}\"\n\n",
                                              "stderr": ESLINT_64},
}


def _rsq_job(tmp_path, template="rsq-node-build"):
    service, _, job, _, repo = running_job(tmp_path)
    pkg = Path(job.worktree) / "resq-management"
    pkg.mkdir()
    (pkg / "package.json").write_text(json.dumps(RSQ_PACKAGE))
    (pkg / "package-lock.json").write_text("{}")
    git(job.worktree, "add", ".")
    git(job.worktree, "commit", "-m", "rsq")
    meta = dict(service.get(job.id).metadata or {})
    meta["build_environments"] = [{"name": template, "source": "template", "ref": template, "status": "ready"}]
    service.store.update_runtime_fields(job.id, metadata_json=meta, validation_json=list(RSQ_RESULTS))
    return service, service.get(job.id)


def test_rsq_cloud_outputs_are_unrunnable_not_preexisting(home, tmp_path):
    from mu.jobs.checks import ChecksStage
    service, job = _rsq_job(tmp_path)
    baseline_runs = []

    def run_suite(job_, workspace, suite):
        if workspace != job.worktree:
            baseline_runs.append(suite.command)
        return dict(RSQ_RESULTS[suite.command])
    run = ChecksStage(service, run_suite=run_suite, settings={}).verify(job)
    assert baseline_runs == []  # never triaged against the base commit
    suites = run.summary["suites"]
    assert [s["status"] for s in suites] == ["unrunnable"] * 3
    assert [s["unrunnable"]["tool"] for s in suites] == ["vitest", "tsc", "eslint"]
    assert all(s["preexisting_failures"] == [] and s["new_failures"] == [] for s in suites)
    assert "comes from node_modules in resq-management, which isn't installed" in suites[0]["unrunnable"]["cause"]
    assert "a tool from outside the project ran instead" in suites[2]["unrunnable"]["cause"]
    assert "`npm --prefix resq-management ci`" in suites[0]["unrunnable"]["hint"]
    assert run.status == "unverified" and run.passed is False and run.verified is False
    assert run.summary["unrunnable"] == 3 and run.summary["verified"] is False
    assert run.summary["preexisting_failures"] == 0
    assert [c.status for c in run.checks] == ["unrunnable"] * 3 and run.checks[0].unrunnable["tool"] == "vitest"


def test_installed_node_modules_make_the_same_failure_a_real_one(home, tmp_path):
    from mu.jobs.checks import Suite, classify_unrunnable
    service, job = _rsq_job(tmp_path)
    bin_dir = Path(job.worktree) / "resq-management" / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    for tool in ("eslint", "prettier"):
        (bin_dir / tool).write_text("")
    lint = Suite("lint", "npm --prefix resq-management run lint")
    assert classify_unrunnable(dict(RSQ_RESULTS[lint.command]), lint, job.worktree) is None
    passing = {"passed": True, "return_code": 0, "stdout": "", "stderr": "sh: 1: x: not found"}
    assert classify_unrunnable(passing, lint, job.worktree) is None
    local = {"passed": False, "return_code": 1, "stdout": "", "stderr": "Error: Cannot find module './widget'"}
    assert classify_unrunnable(local, Suite("t", "python -m pytest"), job.worktree) is None


def test_missing_tool_signatures(home, tmp_path):
    from mu.jobs.checks import Suite, classify_unrunnable
    suite = Suite("t", "make test")
    cases = {
        "bash: line 1: cargo: command not found": "cargo",
        "/bin/sh: 1: pytest: not found": "pytest",
        "Error: Cannot find module 'vitest/config'": "vitest/config",
    }
    for text, tool in cases.items():
        found = classify_unrunnable({"passed": False, "return_code": 1, "stderr": text}, suite, str(tmp_path))
        assert found and found["tool"] == tool and found["kind"] == "missing_tool", text
    registry = classify_unrunnable({"passed": False, "return_code": 1, "stderr": "npm ERR! code E403\nnpm ERR! 403 Forbidden"},
                                   suite, str(tmp_path))
    assert registry["kind"] == "registry" and "registry.npmjs.org" in registry["hint"]
    assert classify_unrunnable({"passed": False, "return_code": 126, "stderr": ""}, suite, str(tmp_path))["kind"] == "not_executable"
    assert classify_unrunnable({"passed": False, "return_code": 127, "stderr": ""}, suite, str(tmp_path))["kind"] == "missing_tool"
    assert classify_unrunnable({"passed": False, "return_code": 1, "stderr": "1 failed"}, suite, str(tmp_path)) is None
    assert classify_unrunnable({"passed": False, "timed_out": True, "return_code": None, "stderr": "x: not found"},
                               suite, str(tmp_path)) is None


def test_unverified_run_parks_with_message_then_continue_caps_review(home, tmp_path):
    from mu.jobs.checks import ChecksStage
    from mu.jobs.core.actions import action_map
    from mu.jobs.core.commands import dispatch
    from mu.jobs.core.vm import facts_for
    from mu.jobs.models import JobStatus
    from mu.jobs.stages import apply_review_verdict
    from mu.jobs.verify_worker import apply_checks_result
    service, job = _rsq_job(tmp_path)
    run = ChecksStage(service, run_suite=lambda j, w, s: dict(RSQ_RESULTS[s.command]), settings={}).verify(job)
    service.transition(job.id, JobStatus.VERIFYING, reason="test")
    assert apply_checks_result(service, job.id, run) == 22
    parked = service.get(job.id)
    assert parked.status == JobStatus.NEEDS_HUMAN and parked.attention_reason.value == "verification_required"
    assert ("`vitest`, `tsc` and `eslint` come from `node_modules`, which isn't installed. Choose a template with job "
            "setup, or add `npm --prefix resq-management ci` to template `rsq-node-build`'s job setup.") in parked.attention_detail
    assert service.store.core_state(job.id).fix_attempts == 0
    service.release(job.id, "worker", reason="test")
    facts = facts_for(service, parked)
    assert facts.checks_unverified and facts.unverified_pending
    actions = action_map(facts)
    assert actions["continueUnverified"].enabled and actions["continueUnverified"].tone == "warn"
    assert "capped at needs work" in actions["continueUnverified"].confirm
    dispatch(service, {"type": "continueUnverified", "job_id": job.id})
    assert service.get(job.id).status == JobStatus.REVIEWING
    apply_review_verdict(service, job.id, {"summary": "Looks fine", "findings": []})
    from mu.jobs.core.artifacts import artifacts_for
    report = artifacts_for(job).read_json("review.json")
    assert report["verdict"] == "needs_work" and report["checks_context"]["unverified"] == 3
    assert report["merge_readiness"] <= 60


def test_review_cap_rules():
    from mu.jobs.core.review_report import compute_verdict
    assert compute_verdict([], [], {"new_failures": 0}) == "ready"
    assert compute_verdict([], [], {"new_failures": 0, "unverified": 2}) == "needs_work"
    assert compute_verdict([], [], {"new_failures": 1, "unverified": 2}) == "do_not_merge"


def test_push_of_unverified_checks_requires_an_override(home, tmp_path):
    from mu.jobs.core.actions import action_map
    from mu.jobs.core.artifacts import artifacts_for
    from mu.jobs.core.commands import CommandRejected, dispatch
    from mu.jobs.core.states import JobState
    from mu.jobs.core.vm import facts_for
    from mu.jobs.models import JobStatus
    from mu.jobs.stages import apply_review_verdict
    from mu.jobs.verification import VerificationRun, VerificationStore
    repo = node_repo(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({"title": "Ticket", "workspace_root": str(repo), "execution": {**EXE, "publish_mode": "gh"}})
    service.store.update_runtime_fields(job.id, branch="mucli/job-abc")
    git(repo, "branch", "mucli/job-abc")
    head = git(repo, "rev-parse", "HEAD")
    summary = {"verified": False, "unrunnable": 1, "new_failures": 0, "suites": []}
    VerificationStore(service.store).save(VerificationRun(
        id="u1", job_id=job.id, status="unverified", passed=False, started_at=1, finished_at=2, duration_ms=1,
        base_sha=head, head_sha=head, branch="mucli/job-abc", worktree=str(repo), summary=summary))
    artifacts_for(job).ensure().write_json("checks/summary.json", summary)
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING, JobStatus.REVIEWING):
        service.transition(job.id, status, reason="test")
    apply_review_verdict(service, job.id, {"summary": "ok", "findings": []})
    assert service.store.core_state(job.id).state == JobState.REVIEW_DONE
    facts = facts_for(service, service.get(job.id))
    push = action_map(facts)["push"]
    assert push.enabled and push.args.get("requires_override") is True and push.args.get("unverified") is True
    with pytest.raises(CommandRejected, match="unverified"):
        dispatch(service, {"type": "push", "job_id": job.id, "args": {}})
    dispatch(service, {"type": "push", "job_id": job.id, "args": {"override_reason": "CI runs the suites"}})
    meta = service.get(job.id).metadata
    assert meta["unverified_override"]["verification_id"] == "u1"
    assert any(e.event_type == "push_override" and e.payload.get("unverified") for e in service.store.recent_events(job.id))


def test_push_check_reason_blocks_unverified_without_allow(home, tmp_path):
    from mu.jobs.checks import push_check_reason
    from mu.jobs.verification import VerificationRun
    run = VerificationRun(id="u", job_id="j", status="unverified", passed=False, started_at=1, finished_at=2,
                          duration_ms=1, base_sha="a", head_sha="a", branch="b", worktree="",
                          summary={"verified": False, "new_failures": 0})
    job = SimpleNamespace(id="j", worktree="", repository=str(tmp_path), branch="b", metadata={})
    assert push_check_reason(None, job, run=run) == "push requires a successful check run"
    failing = replace(run, summary={"verified": False, "new_failures": 2})
    assert push_check_reason(None, job, run=failing, allow_unverified=True) == "push requires a successful check run"


def test_vocabulary_has_unverified_and_unrunnable():
    from mu.design import load_vocabulary
    vocab = load_vocabulary()
    assert vocab["status"]["unverified"] == {"glyph": "?", "semantic": "caution", "label": "unverified"}
    assert vocab["check_status"]["unrunnable"]["status"] == "unverified"
    generated = (ROOT / "mu/gui/static/js/wash_tokens.js").read_text()
    assert '"unverified"' in generated and '"unrunnable"' in generated
    assert '"unverified"' in (ROOT / "mobile/android/src/theme/wash.generated.ts").read_text()


# ------------------------------------------------------------------ runtime image (§5)


def test_runtime_dockerfile_has_no_debian_npm_or_global_dev_tools():
    text = (ROOT / "mu/jobs/sandbox/Dockerfile").read_text()
    apt_lines = " ".join(line for line in re.split(r"(?<!\\)\n", text) if "apt-get install" in line)
    packages = set(re.findall(r"[\w.+-]+", apt_lines.split("apt-get install", 1)[1].split("&&")[0])) if apt_lines else set()
    assert "npm" not in packages and "nodejs" not in packages and "eslint" not in packages
    lowered = text.lower()
    for tool in ("eslint", "prettier", "typescript", "tsc", "vitest", "jest"):
        assert not re.search(rf"npm (?:i|install)\b[^\n]*(?:-g|--global)[^\n]*\b{tool}\b", lowered), tool
        assert not re.search(rf"(?:-g|--global)\s+[^\n]*\b{tool}\b", lowered), tool
    # Node from the official tarball, pinned by version and sha256.
    assert re.search(r"NODE_VERSION=\d+\.\d+\.\d+", text) and "sha256sum -c" in text
    assert re.search(r"node_sha=[0-9a-f]{64}", text) and "nodejs.org/dist/" in text


def test_staleness_digest_tracks_the_dockerfile(tmp_path):
    import shutil
    from mu.jobs.sandbox.build import CODE_FILES, source_digest
    assert "mu/jobs/sandbox/Dockerfile" in CODE_FILES
    source = tmp_path / "src"
    (source / "mu/jobs/sandbox").mkdir(parents=True)
    shutil.copyfile(ROOT / "mu/jobs/sandbox/Dockerfile", source / "mu/jobs/sandbox/Dockerfile")
    (source / "requirements.txt").write_text("x\n")
    before = source_digest(source)
    (source / "mu/jobs/sandbox/Dockerfile").write_text("FROM python:3.12-slim\n")
    assert source_digest(source) != before


def test_setup_events_reach_the_session_view(home, tmp_path, monkeypatch):
    from mu.jobs.core.vm import job_vm
    from mu.jobs.sandbox import job_setup
    from mu.ui.jobs.content import session_items
    monkeypatch.setattr("mu.jobs.sandbox.egress.EgressBridge", lambda d, domains: SimpleNamespace(start=lambda: SimpleNamespace(close=lambda: None)))
    service, job, state = _setup_job(tmp_path)
    job_setup.run_job_setup(service, job, [state], runner=SetupRunner(), settings={})
    session = job_vm(service, job.id, parts=["session"])["session"]
    kinds = [e["event_type"] for e in session["events"]]
    assert kinds.count("environment_setup") == 4
    texts = [i["text"] for i in session_items(session) if i.get("kind") == "chip"]
    assert "setup [rsq] npm --prefix resq-management ci ✓" in texts
