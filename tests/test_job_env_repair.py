"""Environment repair (documentation/plans/job_templates.md §7): checks that could not run get an
unattended repair stage in a repair container, its result is committed as a job-scoped image, the
binding points at it and the checks re-run; a failed repair continues unverified, never parks."""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mu.container import templates as templates_mod
from mu.container.templates import ContainerTemplate, TemplateRegistry
from mu.jobs.models import JobStatus
from test_job_worker import git, running_job


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
    monkeypatch.setattr("mu.jobs.sandbox.egress.EgressBridge",
                        lambda d, domains: SimpleNamespace(start=lambda: SimpleNamespace(close=lambda: None)))
    return tmp_path


STATE = {"name": "node", "source": "template", "ref": "node-build", "paths": [], "status": "ready",
         "job_image": "mucli/job-x-node-runtime:1", "python": "/opt/mucli-venv/bin/python",
         "egress_allow": ["registry.npmjs.org", "api.openai.com", "*.anthropic.com", "proxy.corp.example"],
         "job_setup": ["npm ci"]}
VITEST_MISSING = {"passed": False, "return_code": 127, "stdout": "", "stderr": "sh: 1: vitest: not found"}


def _job(tmp_path, *, state=None, setup_record=True):
    service, _, job, _, _ = running_job(tmp_path)
    (Path(job.worktree) / "package.json").write_text(json.dumps(
        {"name": "x", "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "^2"}}))
    (Path(job.worktree) / "package-lock.json").write_text("{}\n")
    git(job.worktree, "add", ".")
    git(job.worktree, "commit", "-m", "node")
    meta = dict(service.get(job.id).metadata or {})
    if state is not False:
        meta["build_environments"] = [dict(state or STATE)]
        if setup_record:
            meta["environment_setup"] = {"node": {"image": STATE["job_image"], "status": "ok", "files": []}}
    service.store.update_runtime_fields(job.id, metadata_json=meta, validation_json=["npx vitest run"])
    return service, service.get(job.id)


def _unrunnable_run(service, job, result=None):
    from mu.jobs.checks import ChecksStage
    return ChecksStage(service, run_suite=lambda j, w, s: dict(result or VITEST_MISSING), settings={}).verify(job)


class FakeRelay:
    def __init__(self, job, stage, model, policy, art, **kwargs):
        self.stage, self.model, self.policy = stage, model, policy
        self.token, self.cost, self.limit_hit = "stage-token-123", 0.42, None
        FakeRelay.last = self

    def start(self, directory):
        pass

    def close(self):
        pass


class RepairRunner:
    """Fake docker CLI: ``create`` records args; ``start`` plays the repair (writes the result and
    transcript, edits the worktree); ``commit``/``inspect``/``run`` (secret scan) are recorded."""

    def __init__(self, *, result=None, worktree_edit=None, chowned=True, secret="", transcript=None):
        self.result = result or {"kind": "completed", "status": "completed",
                                 "text": "Installed.\nREPAIR COMMANDS:\n```\napt-get install -y jq\nnpm ci\n```"}
        # The shell calls the agent made (the commands it reports must be among them).
        self.transcript = transcript if transcript is not None else [{"role": "assistant", "parts": [
            {"type": "tool_call", "tool_name": "bash", "tool_args": {"command": "apt-get update && apt-get install -y jq"}},
            {"type": "tool_call", "tool_name": "bash", "tool_args": {"command": "npm ci"}}]}]
        self.worktree_edit = worktree_edit
        self.chowned = chowned
        self.secret = secret
        self.calls, self.creates, self.dirs, self.commands = [], [], {}, []

    def require(self, name):
        return "/usr/bin/docker"

    def run(self, args, check=True, timeout=None, input_text=None, output_callback=None):
        args = [str(a) for a in args]
        self.calls.append(args)
        verb = args[1]
        out = ""
        if verb == "create":
            name = args[args.index("--name") + 1]
            mount = lambda dst: next(a.split("src=")[1].split(",")[0] for a in args  # noqa: E731
                                     if a.startswith("type=bind") and f"dst={dst}" in a.split(","))
            self.dirs[name] = (Path(mount("/artifacts")), Path(mount("/output")), Path(mount("/workspace")))
            self.creates.append(args)
        elif verb == "start":
            inp, outp, ws = self.dirs[args[-1]]
            self.request = json.loads((inp / "request.json").read_text())
            if output_callback:
                output_callback("stdout", json.dumps({"event": "tool_call_ui", "payload": {"tool_name": "bash"}}))
            if self.worktree_edit:
                self.worktree_edit(ws)
            (outp / "result.json").write_text(json.dumps(self.result))
            (outp / "transcript.json").write_text(json.dumps(self.transcript))
            if self.chowned:
                (outp / ".chowned").write_text("")
        elif verb == "image" and args[2] == "inspect" and "--format" in args:
            fmt = args[args.index("--format") + 1]
            out = {"{{json .Config.Env}}": json.dumps(["PATH=/usr/bin", "HOME=/root"]), "{{.Config.User}}": "",
                   "{{json .Config.Entrypoint}}": "null", "{{json .Config.Cmd}}": json.dumps(["bash"]),
                   "{{.Id}}": "sha256:repaired"}.get(fmt, "")
        elif verb == "run" and "find" in " ".join(args):
            out = self.secret
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    def commits(self):
        return [c for c in self.calls if c[1] == "commit"]


# ------------------------------------------------------------------ trigger


def test_unrunnable_checks_trigger_a_repair_of_the_owning_binding(home, tmp_path):
    from mu.jobs.sandbox.repair import plan_repair, trigger_suites
    service, job = _job(tmp_path)
    run = _unrunnable_run(service, job)
    assert run.summary["verified"] is False
    suites = trigger_suites(job, run)
    assert [s["suite"] for s in suites] == ["check1"] and suites[0]["kind"] == "node_modules"
    target = plan_repair(service, job, run, settings={})
    assert target["binding"] == "node" and target["image"] == STATE["job_image"] and target["attempt"] == 1
    # Disabled, or a verified run: nothing to repair.
    assert plan_repair(service, job, run, settings={"jobs": {"repair": {"enabled": False}}}) is None
    passing = _unrunnable_run(service, job, {"passed": True, "return_code": 0, "stdout": "1 passed"})
    assert trigger_suites(job, passing) == [] and plan_repair(service, job, passing, settings={}) is None


def test_a_binding_that_is_not_prepared_is_not_repaired(home, tmp_path):
    from mu.jobs.sandbox.repair import plan_repair
    service, job = _job(tmp_path, state={**STATE, "status": "cleaned"})
    run = _unrunnable_run(service, job)
    assert plan_repair(service, job, run, settings={}) is None


def test_missing_top_level_tool_routes_into_repair_instead_of_parking(home, tmp_path):
    """The former "checks need X" missing-tool path: a missing executable in the shared runtime
    repairs the runtime (job-scoped image), and a repair that doesn't fix it continues unverified."""
    from mu.jobs.sandbox.repair import repair_and_recheck
    from mu.jobs.verify_worker import apply_checks_result
    service, job = _job(tmp_path, state=False)
    job = replace(job, validation_commands=["cargo test"])
    cargo = {"passed": False, "return_code": 127, "stdout": "", "missing_executable": "cargo",
             "stderr": "cargo: not installed in the job sandbox image mucli-job-runtime:latest"}
    run = _unrunnable_run(service, job, cargo)
    assert run.summary["missing_tools"] == ["cargo"]
    targets = []

    def fake_repair(service_, job_, run_, target, **kwargs):
        targets.append(target)
        return {"attempt": target["attempt"], "outcome": "failed", "error": "apt-get: network unreachable"}
    out = repair_and_recheck(service, job.id, run, verify=lambda j: pytest.fail("no re-run after a failed repair"),
                             settings={}, repair=fake_repair)
    assert out is run and len(targets) == 1
    assert targets[0]["binding"] == "runtime" and targets[0]["state"] is None
    assert targets[0]["image"] == "mucli-job-runtime:latest"
    service.transition(job.id, JobStatus.VERIFYING, reason="test")
    assert apply_checks_result(service, job.id, run) == 0
    assert service.get(job.id).status == JobStatus.REVIEWING  # never parks


def test_environment_symptom_failures_continue_unverified_instead_of_parking(home, tmp_path):
    from mu.jobs.sandbox.repair import trigger_suites
    from mu.jobs.verification import VerificationRun
    from mu.jobs.verify_worker import apply_checks_result
    service, job = _job(tmp_path, state=False)
    suite = {"suite": "unit", "command": "pytest", "new_failures": ["unit"], "environment": "",
             "stderr": "ModuleNotFoundError: No module named 'requests'"}
    run = VerificationRun(id="r", job_id=job.id, status="failed", passed=False, started_at=1, finished_at=2,
                          duration_ms=1, base_sha=job.base_sha, head_sha=job.base_sha, branch=job.branch,
                          worktree=job.worktree, summary={"new_failures": 1, "suites": [suite], "trigger": "auto"})
    assert [s["kind"] for s in trigger_suites(job, run)] == ["environment"]
    service.transition(job.id, JobStatus.VERIFYING, reason="test")
    assert apply_checks_result(service, job.id, run) == 0
    current = service.get(job.id)
    assert current.status == JobStatus.REVIEWING and (current.metadata or {}).get("checks_unverified")
    note = [e for e in service.events(job.id) if e.event_type == "checks_unverified"][-1]
    assert "No module named" in note.reason


# ------------------------------------------------------------------ isolation and egress


def test_repair_container_args_isolation(home, tmp_path, monkeypatch):
    from mu.jobs.sandbox.policy import REPAIR_CAPS
    from mu.jobs.sandbox.repair import repair_container_args, repair_policy
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should-never-leak")
    service, job = _job(tmp_path)
    target = {"binding": "node", "state": STATE, "image": STATE["job_image"], "suites": [], "attempt": 1}
    policy, _added = repair_policy(job, target, {"sandbox": {"egress_extra": []}}, detected=["registry.npmjs.org"])
    dirs = []
    for name in ("input", "output", "relay"):
        (tmp_path / name).mkdir()
        dirs.append(tmp_path / name)
    args = repair_container_args(job, dirs, "mucli-job-x-repair-1", policy, "tok")
    text = " ".join(args)
    # Root inside, every capability dropped but the package-manager set, no privilege gain.
    assert args[args.index("--user") + 1] == "0:0" and text.count("--user") == 1
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert [args[i + 1] for i, a in enumerate(args) if a == "--cap-add"] == list(REPAIR_CAPS)
    assert set(REPAIR_CAPS) == {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "FSETID"}
    assert "no-new-privileges:true" in text
    # Writable rootfs, tmpfs /tmp, network none (egress only through the bridge).
    assert "--read-only" not in args and "/tmp:rw,nosuid,nodev" in text
    assert args[args.index("--network") + 1] == "none"
    # Worktree read-write, /cache mounted; no Docker socket, no GPUs, no credentials.
    ws = next(a for a in args if a.startswith("type=bind") and a.endswith("dst=/workspace"))
    assert "readonly" not in ws and any(a.endswith("dst=/cache") for a in args if a.startswith("type=bind"))
    assert "docker.sock" not in text and "--gpus" not in text and "DOCKER_HOST" not in text
    assert "sk-should-never-leak" not in text and "ghp_" not in text and "ANTHROPIC" not in text
    assert not any(a.endswith(("dst=/harness/creds", "dst=/root/.npmrc")) for a in args)
    # Model via the relay only; job-labelled; Claude Code allowed to run as root in the sandbox.
    assert "MUCLI_MODEL_RELAY=/relay/model.sock" in text and "MUCLI_JOB_STAGE=repair" in text
    assert "io.mucli.role=job-repair" in text and f"io.mucli.job={job.id}" in text
    assert "IS_SANDBOX=1" in text and f"MUCLI_REPAIR_CHOWN={os.getuid()}:{os.getgid()}" in text
    # The entrypoint runs the worker, then chowns the mounts back to the host user.
    entry = args.index("--entrypoint")
    assert args[entry + 1:entry + 4] == ["/bin/sh", STATE["job_image"], "-c"]
    assert "chown -R" in args[entry + 4] and "/workspace /cache" in args[entry + 4]
    tail = args[entry + 5:]
    assert tail[:2] == ["/opt/mucli-venv/bin/python", "mu.jobs.sandbox.worker"]
    # The stage environment travels as wrapper arguments, never as ``-e`` (the container is
    # committed; its Env would carry the relay token into the image).
    assert "-e" not in args and "MUCLI_STAGE_TOKEN=tok" in tail and "HOME=/tmp" in tail
    assert args.index("MUCLI_STAGE_TOKEN=tok") > entry
    # A Docker socket or GPUs are refused outright.
    from mu.jobs.sandbox.policy import container_args
    with pytest.raises(ValueError):
        container_args("n", "repair", job.worktree, *map(str, dirs), replace(policy, docker_socket="/x.sock"), "")


def test_egress_composition_auto_vs_template_and_model_hosts_dropped(home, tmp_path):
    from mu.jobs.sandbox.repair import OS_MIRRORS, TOOLCHAIN_INSTALLERS, repair_egress, repair_policy
    allow = ["proxy.corp.example", "api.openai.com", "*.anthropic.com", "generativelanguage.googleapis.com"]
    auto = repair_egress(allow, registries="auto", detected=["pypi.org", "registry.npmjs.org"])
    assert auto[:1] == ["proxy.corp.example"] and "pypi.org" in auto and "registry.npmjs.org" in auto
    assert all(d in auto for d in (*OS_MIRRORS, *TOOLCHAIN_INSTALLERS))
    assert not any("openai" in d or "anthropic" in d or "googleapis" in d for d in auto)
    template = repair_egress(allow, registries="template", detected=["pypi.org"])
    assert "pypi.org" not in template and "deb.debian.org" in template and "github.com" in template
    # Through the policy: the binding's allowlist (model hosts dropped) ∪ detected registries.
    service, job = _job(tmp_path)
    target = {"binding": "node", "state": STATE, "image": STATE["job_image"], "suites": [], "attempt": 1}
    policy, added = repair_policy(job, target, {"sandbox": {"egress_extra": []}}, detected=["crates.io", "registry.npmjs.org"])
    assert "crates.io" in policy.egress_extra and "proxy.corp.example" in policy.egress_extra
    assert added == ["crates.io"]  # registry.npmjs.org was already the binding's
    assert not any("openai" in d or "anthropic" in d for d in policy.egress_extra)
    assert policy.timeout == 1200 and policy.max_cost_usd == 3.0 and policy.image == STATE["job_image"]
    policy, added = repair_policy(job, target, {"sandbox": {"egress_extra": []}, "jobs": {"repair": {"registries": "template"}}},
                                  detected=["crates.io"])
    assert "crates.io" not in policy.egress_extra and added == []


# ------------------------------------------------------------------ worktree hygiene


def test_tracked_file_changes_are_reverted_but_never_the_implement_work(home, tmp_path):
    from mu.jobs.sandbox.repair import _snapshot, _status, revert_tracked_changes
    service, job = _job(tmp_path, state=False)
    ws = Path(job.worktree)
    (ws / "src.txt").write_text("base\n")
    (ws / "gone.txt").write_text("keep me\n")
    git(ws, "add", ".")
    git(ws, "commit", "-m", "files")
    (ws / "src.txt").write_text("implement's change\n")      # the implement stage's work
    (ws / "new_feature.py").write_text("print('new')\n")     # implement's new file
    big = b"implement asset " * (6 * 1024 * 1024 // 16)        # above the old 4 MiB snapshot cap
    (ws / "src.txt").write_bytes(b"implement's change\n")
    (ws / "asset.bin").write_bytes(big)
    git(ws, "add", "asset.bin")
    before = _status(str(ws))
    saved = _snapshot(str(ws), before, tmp_path / "snapshot")
    # The repair: rewrites the manifest, edits implement's files, deletes a tracked file, installs.
    (ws / "package.json").write_text("{}")
    (ws / "asset.bin").write_bytes(b"clobbered")
    (ws / "src.txt").write_text("repair clobbered this\n")
    (ws / "gone.txt").unlink()
    (ws / "vendor-cache").mkdir()
    (ws / "vendor-cache" / "blob.bin").write_text("x")
    result = revert_tracked_changes(str(ws), before, saved)
    assert sorted(result["reverted"]) == ["asset.bin", "gone.txt", "package.json", "src.txt"]
    assert (ws / "asset.bin").read_bytes() == big  # large files are protected too
    assert (ws / "src.txt").read_text() == "implement's change\n"   # back to implement's version
    assert (ws / "gone.txt").read_text() == "keep me\n"
    assert "vitest" in (ws / "package.json").read_text()
    assert (ws / "new_feature.py").read_text() == "print('new')\n"
    assert result["files"] == ["vendor-cache/blob.bin"]


# ------------------------------------------------------------------ the repair stage


def _target(service, job, run):
    from mu.jobs.sandbox.repair import plan_repair
    return plan_repair(service, job, run, settings={"sandbox": {"egress_extra": []}})


def test_repair_commits_image_updates_binding_and_later_checks_use_it(home, tmp_path):
    from mu.jobs.core.vm import job_vm
    from mu.jobs.sandbox.environments import job_states
    from mu.jobs.sandbox.job_setup import setup_records
    from mu.jobs.sandbox.policy import SandboxPolicy, with_build_environment
    from mu.jobs.sandbox.repair import repair_and_recheck, repair_records
    service, job = _job(tmp_path)
    run = _unrunnable_run(service, job)

    def edit(ws):
        (ws / "package.json").write_text("{}")  # a tracked change the host must revert
    runner = RepairRunner(worktree_edit=edit)
    verified = _unrunnable_run(service, job, {"passed": True, "return_code": 0, "stdout": "1 passed"})
    seen = []

    def verify(fresh):
        seen.append((fresh.metadata or {}).get("checks_trigger"))
        return verified
    out = repair_and_recheck(service, job.id, run, verify=verify, runner=runner, relay_factory=FakeRelay,
                             settings={"sandbox": {"egress_extra": []}})
    assert out is verified and seen == ["repair"]  # the re-run is triggered by the repair
    tag = f"mucli/job-{job.id[:12]}-node-repair1"
    commit = runner.commits()[0]
    assert commit[-1] == tag and commit[-2].startswith("mucli-job-")
    changes = " ".join(commit)
    assert f'io.mucli.job="{job.id}"' in changes and 'io.mucli.role="job-image"' in changes
    assert "stage-token-123" not in changes and "MUCLI_STAGE_TOKEN" not in changes  # nothing to scrub
    assert "-e" not in runner.creates[0] and "MUCLI_STAGE_TOKEN=stage-token-123" in runner.creates[0]
    assert 'CMD ["bash"]' in changes and "ENTRYPOINT []" in changes
    # The binding points at the repaired image; setup is not re-run for it.
    fresh = service.get(job.id)
    state = job_states(fresh)[0]
    assert state["job_image"] == tag and state["repaired_from"] == STATE["job_image"]
    assert setup_records(fresh)["node"]["image"] == tag
    policy = with_build_environment(SandboxPolicy.for_stage("checks", {}), fresh, state, stage="checks", settings={})
    assert policy.image == tag  # every later check (and stage) runs the repaired image
    # The request: the implement agent's model, unattended, with the repair budget and prompt.
    assert FakeRelay.last.stage == "repair" and FakeRelay.last.model["model"] == "test"
    assert runner.request["max_iterations"] == 40 and runner.request["auto_approve_writes"] is True
    assert "RUNNABLE" in runner.request["prompt"] and "vitest" in runner.request["prompt"]
    assert "REPAIR COMMANDS" in runner.request["prompt"]
    # Record and events.
    record = repair_records(fresh)[0]
    assert record["outcome"] == "repaired" and record["image"] == tag and record["attempt"] == 1
    assert record["commands"] == ["apt-get install -y jq", "npm ci"] and record["project_commands"] == ["npm ci"]
    assert record["reverted_files"] == ["package.json"] and record["cost_usd"] == pytest.approx(0.42)
    assert record["trigger_suites"][0]["suite"] == "check1" and record["checks_after"]["verified"] is True
    assert set(record) >= {"attempt", "binding", "trigger_suites", "commands", "image", "cost_usd", "outcome",
                           "reverted_files", "started_at", "finished_at"}
    assert "vitest" in (Path(job.worktree) / "package.json").read_text()  # reverted
    phases = [e.payload["phase"] for e in service.events(fresh.id) if e.event_type == "environment_repair"]
    assert phases[0] == "started" and phases.count("command") == 2 and phases[-1] == "finished"
    finished = [e for e in service.events(fresh.id) if e.event_type == "environment_repair"][-1]
    assert finished.payload["outcome"] == "repaired" and "Environment repaired" in finished.reason
    live = [e.payload for e in service.events(fresh.id) if e.event_type == "sandbox_event"]
    assert live and live[0]["stage"] == "repair"
    # The job page: Checks tab card data, transcript in the session view, environments panel.
    vm = job_vm(service, job.id)
    assert vm["checks"]["repairs"][0]["outcome"] == "repaired"
    assert vm["session"]["stages"]["repair"] and vm["build_environments"]["repairs"][0]["image"] == tag
    assert any(e["event_type"] == "environment_repair" for e in vm["session"]["events"])


def test_repair_of_the_shared_runtime_sets_the_job_runtime_image(home, tmp_path):
    from mu.jobs.sandbox.policy import SandboxPolicy, with_build_environment
    from mu.jobs.sandbox.repair import run_repair
    service, job = _job(tmp_path, state=False)
    run = _unrunnable_run(service, job)
    record = run_repair(service, job, run, _target(service, job, run), runner=RepairRunner(), relay_factory=FakeRelay,
                        settings={"sandbox": {"egress_extra": []}})
    tag = f"mucli/job-{job.id[:12]}-runtime-repair1"
    assert record["outcome"] == "committed" and record["image"] == tag
    fresh = service.get(job.id)
    assert fresh.metadata["runtime_repair_image"] == tag
    assert with_build_environment(SandboxPolicy.for_stage("implement", {}), fresh, None, stage="implement").image == tag


def test_job_cleanup_removes_the_repaired_image_and_drops_the_runtime_key(home, tmp_path):
    from mu.jobs.sandbox.environments import cleanup_job
    service, job = _job(tmp_path, state=False)
    service.store.patch_metadata(job.id, {"runtime_repair_image": "mucli/job-x-runtime-repair1"})

    class Docker:
        def __init__(self):
            self.calls = []

        @property
        def binary(self):
            return "docker"

        def run(self, *args, **kwargs):
            self.calls.append(args)
            out = "sha256:repaired\n" if args[:2] == ("image", "ls") and f"label=io.mucli.job={job.id}" in args else ""
            return SimpleNamespace(returncode=0, stdout=out, stderr="")

        def inspect(self, *args):
            return "job-image"
    docker = Docker()
    cleanup_job(job.id, service=service, docker=docker, reason="job done")
    assert ("image", "rm", "-f", "sha256:repaired") in [c[:4] for c in docker.calls]
    assert "runtime_repair_image" not in (service.get(job.id).metadata or {})


def test_repair_failure_timeout_and_budget_record_the_outcome_and_continue_unverified(home, tmp_path):
    from mu.jobs.sandbox.repair import repair_and_recheck, repair_records, run_repair
    from mu.jobs.verify_worker import apply_checks_result
    service, job = _job(tmp_path)
    run = _unrunnable_run(service, job)
    failing = RepairRunner(result={"kind": "failed", "status": "error", "error": "E: Unable to locate package"})
    out = repair_and_recheck(service, job.id, run, verify=lambda j: pytest.fail("no re-run"), runner=failing,
                             relay_factory=FakeRelay, settings={"sandbox": {"egress_extra": []}})
    assert out is run and not failing.commits()
    record = repair_records(service.get(job.id))[-1]
    assert record["outcome"] == "failed" and "Unable to locate package" in record["error"]
    assert record["finished_at"] and record["image"] == ""
    finished = [e for e in service.events(job.id) if e.event_type == "environment_repair"][-1]
    assert finished.payload["outcome"] == "failed" and "continues unverified" in finished.reason
    service.transition(job.id, JobStatus.VERIFYING, reason="test")
    assert apply_checks_result(service, job.id, run) == 0
    assert service.get(job.id).status == JobStatus.REVIEWING  # no park
    note = [e for e in service.events(job.id) if e.event_type == "checks_unverified"][-1]
    assert "Environment repair: 1 attempt(s); the last ended failed" in note.reason
    # Budget: the iteration cap and a relay budget refusal end the repair without a commit.
    (tmp_path / "b").mkdir()
    service2, job2 = _job(tmp_path / "b")
    run2 = _unrunnable_run(service2, job2)
    capped = RepairRunner(result={"kind": "needs_human", "status": "max_iterations_reached", "error": ""})
    record = run_repair(service2, job2, run2, _target(service2, job2, run2), runner=capped, relay_factory=FakeRelay,
                        settings={"sandbox": {"egress_extra": []}})
    assert record["outcome"] == "budget_exhausted" and not capped.commits()

    class BrokeRelay(FakeRelay):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.limit_hit = {"kind": "cost", "message": "Stage cost budget exhausted: $3.00"}
    run3 = _unrunnable_run(service2, service2.get(job2.id))
    record = run_repair(service2, service2.get(job2.id), run3, _target(service2, service2.get(job2.id), run3),
                        runner=RepairRunner(), relay_factory=BrokeRelay, settings={"sandbox": {"egress_extra": []}})
    assert record["outcome"] == "budget_exhausted" and "budget" in record["error"]


def test_repair_timeout_is_a_recorded_failure(home, tmp_path, monkeypatch):
    from mu.jobs.sandbox import repair
    service, job = _job(tmp_path)
    run = _unrunnable_run(service, job)
    monkeypatch.setattr(repair, "_supervise", lambda *a, **k: "timed_out")
    runner = RepairRunner(chowned=False)
    record = repair.run_repair(service, job, run, _target(service, job, run), runner=runner, relay_factory=FakeRelay,
                               settings={"sandbox": {"egress_extra": []}})
    assert record["outcome"] == "timed_out" and not runner.commits()
    # The repair was stopped before its entrypoint chowned the mounts: a helper container does it.
    chown = [c for c in runner.calls if c[1] == "run" and "chown" in c]
    assert chown and f"{os.getuid()}:{os.getgid()}" in chown[0] and "--network" in chown[0]


def test_a_repaired_image_with_secret_files_is_discarded(home, tmp_path):
    from mu.jobs.sandbox.environments import job_states
    from mu.jobs.sandbox.repair import run_repair
    service, job = _job(tmp_path)
    run = _unrunnable_run(service, job)
    runner = RepairRunner(secret="/root/.ssh/id_rsa\n")
    record = run_repair(service, job, run, _target(service, job, run), runner=runner, relay_factory=FakeRelay,
                        settings={"sandbox": {"egress_extra": []}})
    assert record["outcome"] == "failed" and "secret" in record["error"]
    assert job_states(service.get(job.id))[0]["job_image"] == STATE["job_image"]


def test_max_attempts_per_job(home, tmp_path):
    from mu.jobs.sandbox.repair import plan_repair, repair_and_recheck, repair_records
    service, job = _job(tmp_path)
    run = _unrunnable_run(service, job)
    calls = []

    def verify(fresh):
        calls.append(1)
        return _unrunnable_run(service, fresh)  # still unrunnable after every repair
    repair_and_recheck(service, job.id, run, verify=verify, runner=RepairRunner(), relay_factory=FakeRelay,
                       settings={"sandbox": {"egress_extra": []}})
    records = repair_records(service.get(job.id))
    assert [r["attempt"] for r in records] == [1, 2] and len(calls) == 2
    assert [r["outcome"] for r in records] == ["unresolved", "unresolved"]
    assert records[1]["image"].endswith("-node-repair2")
    assert plan_repair(service, service.get(job.id), run, settings={}) is None
    assert plan_repair(service, service.get(job.id), run, settings={"jobs": {"repair": {"max_attempts": 3}}})


def test_repair_cost_counts_toward_the_job_cost(home, tmp_path):
    from mu.jobs.core.artifacts import artifacts_for
    from mu.jobs.sandbox.repair import run_repair

    class LedgerRelay(FakeRelay):
        def __init__(self, job, stage, model, policy, art, **kwargs):
            super().__init__(job, stage, model, policy, art, **kwargs)
            art.append_cost({"stage": stage, "at": 1.0, "cost_usd": 0.42, "provider": "openai", "model": "test"})
    service, job = _job(tmp_path)
    run = _unrunnable_run(service, job)
    run_repair(service, job, run, _target(service, job, run), runner=RepairRunner(), relay_factory=LedgerRelay,
               settings={"sandbox": {"egress_extra": []}})
    assert artifacts_for(job).stage_costs()["repair"] == pytest.approx(0.42)
    assert service.get(job.id).cost_usd == pytest.approx(0.42)


def test_prompt_lists_unrunnable_commands_tails_detection_and_ci_hints(home, tmp_path):
    from mu.jobs.sandbox.repair import build_prompt, parse_commands, project_commands
    service, job = _job(tmp_path)
    workflows = Path(job.worktree) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("steps:\n  - run: npm ci\n")
    run = _unrunnable_run(service, job)
    target = _target(service, job, run)
    prompt = build_prompt(job, run, target, detected=[{"name": "node-npm", "manifest": "package.json",
                                                      "lockfile": "package-lock.json"}])
    assert "`npx vitest run`" in prompt and "vitest: not found" in prompt
    assert "node-npm (package.json)" in prompt and ".github/workflows/ci.yml" in prompt and "run: npm ci" in prompt
    assert "Do not modify tracked files" in prompt and "apt-get" in prompt
    assert parse_commands("done") == []
    assert parse_commands("x", [{"parts": [{"type": "tool_call", "tool_name": "bash",
                                            "tool_args": {"command": "uv sync"}}]}]) == ["uv sync"]
    assert project_commands(["npm ci", "apt-get install -y libssl-dev", "npm install -g pnpm", "uv sync",
                             "sudo npm ci", "curl -fsSL https://sh.rustup.rs | sh"]) == ["npm ci", "uv sync"]


# ------------------------------------------------------------------ actions


def _repaired(service, job, **record):
    base = {"attempt": 1, "binding": "node", "source": "template", "template": "node-build", "outcome": "repaired",
            "image": f"mucli/job-{job.id[:12]}-node-repair1", "commands": ["apt-get install -y jq", "npm ci"],
            "project_commands": ["npm ci"], "registries": ["crates.io"], "files": []}
    service.store.patch_metadata(job.id, {"environment_repairs": [{**base, **record}]})
    return service.get(job.id)


def test_add_repair_to_template_appends_project_commands(home, tmp_path):
    from mu.jobs.core.actions import action_map
    from mu.jobs.core.commands import dispatch
    from mu.jobs.core.vm import facts_for
    TemplateRegistry().upsert(ContainerTemplate(name="node-build", image="mucli/template-node-build:1",
                                                source_container="src", job_setup=["npm ci", "echo ready"]))
    service, job = _job(tmp_path)
    actions = action_map(facts_for(service, job))
    assert not actions["addRepairToTemplate"].enabled and actions["addRepairToTemplate"].hidden
    job = _repaired(service, job, project_commands=["npm ci", "npm --prefix web ci"])
    action = action_map(facts_for(service, job))["addRepairToTemplate"]
    assert action.enabled and action.args == {"attempt": 1}
    result = dispatch(service, {"type": "addRepairToTemplate", "job_id": job.id, "args": {"attempt": 1}})
    assert result.data["added"] == ["npm --prefix web ci"]
    assert TemplateRegistry().get("node-build").job_setup == ["npm ci", "echo ready", "npm --prefix web ci"]
    assert [e for e in service.events(job.id) if e.event_type == "environment_repair_saved"]


def test_add_repair_to_template_is_only_valid_for_template_sources(home, tmp_path):
    from mu.jobs.core.actions import action_map
    from mu.jobs.core.commands import CommandRejected, dispatch
    from mu.jobs.core.vm import facts_for
    from mu.jobs.sandbox.repair import add_repair_to_template
    service, job = _job(tmp_path)
    job = _repaired(service, job, source="image", template="")
    action = action_map(facts_for(service, job))["addRepairToTemplate"]
    assert not action.enabled and action.reason == "the repaired build environment is not a template"
    with pytest.raises(CommandRejected):
        dispatch(service, {"type": "addRepairToTemplate", "job_id": job.id})
    with pytest.raises(ValueError):
        add_repair_to_template(job)
    job = _repaired(service, job, project_commands=[])
    assert action_map(facts_for(service, job))["addRepairToTemplate"].reason == "the repair used no project-level commands"
    # A failed repair committed nothing: neither action applies.
    job = _repaired(service, job, outcome="failed", image="")
    actions = action_map(facts_for(service, job))
    assert not actions["addRepairToTemplate"].enabled and not actions["saveRepairedTemplate"].enabled


def test_save_repaired_template_creates_a_container_template(home, tmp_path, monkeypatch):
    from mu.jobs.core.actions import action_map
    from mu.jobs.core.commands import CommandError, dispatch
    from mu.jobs.core.vm import facts_for
    from mu.jobs.sandbox import environments as envs
    builds = []

    class Docker:
        def run(self, *args, **kwargs):
            if args[0] == "build":
                builds.append((args, Path(args[-1], "Dockerfile").read_text()))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(envs, "Docker", lambda *a, **k: Docker())
    service, job = _job(tmp_path)
    job = _repaired(service, job)
    action = action_map(facts_for(service, job))["saveRepairedTemplate"]
    assert action.enabled and action.input["value"] == "node-build-repaired" and action.input["arg"] == "name"
    result = dispatch(service, {"type": "saveRepairedTemplate", "job_id": job.id, "args": {}})
    template = TemplateRegistry().get("node-build-repaired")
    assert template is not None and result.data["template"] == "node-build-repaired"
    assert template.image.startswith("mucli/template-node-build-repaired:")
    assert template.egress_allow == ["registry.npmjs.org", "proxy.corp.example", "crates.io"]  # model hosts dropped
    assert template.job_setup == ["npm ci"]
    args, dockerfile = builds[0]
    assert dockerfile.startswith(f"FROM mucli/job-{job.id[:12]}-node-repair1\n")
    assert 'io.mucli.job=""' in dockerfile and 'io.mucli.template="node-build-repaired"' in dockerfile
    with pytest.raises(CommandError):
        dispatch(service, {"type": "saveRepairedTemplate", "job_id": job.id, "args": {"name": "node-build-repaired"}})
    dispatch(service, {"type": "saveRepairedTemplate", "job_id": job.id, "args": {"name": "My Fixed Env"}})
    assert TemplateRegistry().get("my-fixed-env") is not None


# ------------------------------------------------------------------ settings and stage name


def test_repair_settings_schema(home):
    from mu.jobs.job_settings import FIELD_BY_PATH, public_settings, save_settings, validate_rules
    from mu.jobs.sandbox.repair import repair_settings
    expected = {"jobs.repair.enabled": True, "jobs.repair.max_attempts": 2, "jobs.repair.registries": "auto",
                "jobs.repair.max_cost_usd": 3.0, "jobs.repair.max_runtime_s": 1200, "jobs.repair.max_iterations": 40}
    for path, default in expected.items():
        assert FIELD_BY_PATH[path]["default"] == default
    assert [o[0] for o in FIELD_BY_PATH["jobs.repair.registries"]["options"]] == ["auto", "template"]
    sections = {s["id"]: s for s in public_settings()["rules"]["schema"]}
    assert [f["path"] for f in sections["repair"]["fields"]] == list(expected)
    with pytest.raises(ValueError):
        validate_rules({"jobs": {"repair": {"registries": "everything"}}})
    with pytest.raises(ValueError):
        validate_rules({"jobs": {"repair": {"max_attempts": 99}}})
    assert repair_settings({}) == {"enabled": True, "max_attempts": 2, "registries": "auto", "max_cost_usd": 3.0,
                                   "max_runtime_s": 1200, "max_iterations": 40}
    save_settings({"jobs": {"repair": {"max_attempts": 1, "registries": "template"}}})
    assert repair_settings()["max_attempts"] == 1 and repair_settings()["registries"] == "template"


def test_repair_is_a_writing_stage(home):
    from mu.jobs.sandbox.policy import STAGES, SandboxPolicy
    from mu.jobs.unattended import WRITE_STAGES, decide, interaction_response
    assert "repair" in STAGES and "repair" in WRITE_STAGES
    assert decide("repair") == (True, "")
    assert interaction_response("repair", "approval_required")["decision"] == "approve"
    policy = SandboxPolicy.for_stage("repair", {})
    assert policy.egress_extra  # Settings → Extra egress domains apply, as for implement


# ------------------------------------------------------------------ surfaces


ROOT = Path(__file__).resolve().parents[1]


def test_vocabulary_has_repair_outcomes_for_every_outcome():
    from mu.design import load_vocabulary
    from mu.jobs.sandbox.repair import OUTCOMES
    vocab = load_vocabulary()
    rows = vocab["repair_outcome"]
    assert set(OUTCOMES) | {"committed"} == set(rows)
    assert all(row["status"] in vocab["status"] for row in rows.values())
    assert rows["repaired"]["status"] == "done" and rows["failed"]["status"] == "failed"
    assert rows["unresolved"]["status"] == "unverified"


def test_web_job_page_repair_card_contract():
    env = (ROOT / "mu/gui/static/js/work/environments.js").read_text()
    view = (ROOT / "mu/gui/static/js/work/view.js").read_text()
    # One shared card (Wash components, shared vocabulary) for the Checks tab and the panel.
    assert "function repairCard(" in env and "vocab.repair_outcome" in env and "w-card work-card work-repair" in env
    assert "data-repair-card" in env and "data-repair-transcript" in env and "'Repair failed'" in env
    assert "Environment repaired" in env and "repairCard, repairOutcome" in env
    assert "ENV.repairCard(repairs, {buttons:repairButtons()})" in view
    # A failed or ended repair is hidden once a later run verified the checks (it is history).
    assert "repairSuperseded" in view and "!isUnverified(latest)" in view
    assert "ENV.panel(job.build_environments, buttons);" in view and "[data-repair-actions]" in view
    assert "data-repair-actions" in env
    assert "const REPAIR_ACTIONS = ['addRepairToTemplate', 'saveRepairedTemplate'];" in view
    assert "saveRepairedTemplate:'name'" in view  # the typed template name goes to args.name
    assert "sessionStage = 'repair'" in view and "['repair', 'Repair']" in view
    assert "e.event_type === 'environment_repair'" in view


def test_terminal_checks_view_shows_a_repair_line():
    from mu.ui.jobs.content import checks_lines, session_items
    checks = {"runs": [], "repairs": [{"attempt": 1, "binding": "node", "outcome": "repaired", "cost_usd": 0.42,
                                       "commands": ["npm ci"]},
                                      {"attempt": 2, "binding": "runtime", "outcome": "failed", "commands": [],
                                       "error": "apt-get: unable to locate package", "reverted_files": ["package.json"]}]}
    text = ["".join(t for _s, t in line) for line in checks_lines(checks, "reviewing", 100, 0, 0)]
    assert "Environment repair" in text
    line = next(t for t in text if "repair failed" in t)
    assert "✗" in line and "attempt 2 · shared runtime" in line
    assert any("unable to locate package" in t for t in text) and any("reverted tracked files" in t for t in text)
    one = {"runs": [], "repairs": checks["repairs"][:1]}
    text = ["".join(t for _s, t in line) for line in checks_lines(one, "reviewing", 100, 0, 0)]
    assert any("✓ environment repaired · attempt 1 · node · $0.42" in t for t in text)
    assert any("$ npm ci" in t for t in text)
    items = session_items({"events": [
        {"event_type": "environment_repair", "reason": "repairing", "payload": {"phase": "started", "binding": "node"}},
        {"event_type": "environment_repair", "payload": {"phase": "command", "binding": "node", "command": "npm ci"}},
        {"event_type": "environment_repair", "reason": "did not finish", "payload": {"phase": "finished", "outcome": "failed"}}]})
    assert [i["kind"] for i in items[-3:]] == ["chip", "chip", "error"] and "$ npm ci" in items[-2]["text"]


def test_repair_events_refresh_the_checks_and_session_parts(home, tmp_path):
    from mu.jobs.core.changes import ChangeBus, collect_durable_changes
    service, job = _job(tmp_path, state=False)
    cursor = service.store.event_cursor()
    service.store.append_event(job.id, "environment_repair", payload={"phase": "started"})
    bus = ChangeBus(window_s=0)
    collect_durable_changes(service, bus, cursor)
    parts = {p for change in bus.flush(force=True) for p in change.parts}
    assert {"checks", "session", "state"} <= parts


# ------------------------------------------------------------------ only what actually ran


def test_parse_commands_counts_only_commands_that_ran():
    from mu.jobs.sandbox.repair import parse_commands
    text = "REPAIR COMMANDS:\n```\napt-get install -y nodejs\nnpm ci\n```"
    ran = [{"parts": [{"type": "tool_call", "tool_name": "bash", "tool_args": {"command": "cd /workspace &&  npm ci"}}]}]
    assert parse_commands(text, ran) == ["npm ci"]
    assert parse_commands(text, []) == []  # a transcript with no shell calls: nothing ran
    assert parse_commands(text) == ["apt-get install -y nodejs", "npm ci"]  # no transcript: the block


def test_a_repair_that_ran_no_commands_commits_nothing(home, tmp_path):
    """A repair agent without a shell (an image whose baked worker ran repair read-only) can only
    suggest commands; the host must not commit an unchanged image and call it repaired."""
    from mu.jobs.sandbox.repair import repair_and_recheck, repair_records
    service, job = _job(tmp_path)
    run = _unrunnable_run(service, job)
    runner = RepairRunner(transcript=[{"role": "assistant", "parts": [
        {"type": "tool_call", "tool_name": "read_file", "tool_args": {"filename": "package.json"}}]}],
        result={"kind": "completed", "status": "completed",
                "text": "I couldn't run anything.\nREPAIR COMMANDS:\n```\napt-get install -y nodejs\n```"})
    rechecks = []
    out = repair_and_recheck(service, job.id, run, verify=lambda fresh: rechecks.append(1) or run, runner=runner,
                             relay_factory=FakeRelay, settings={"sandbox": {"egress_extra": []}})
    assert out is run and not runner.commits()
    record = repair_records(service.get(job.id))[0]
    assert record["outcome"] == "no_commands" and record["commands"] == []
    assert "ran no shell commands" in record["error"] and "apt-get install -y nodejs" in record["error"]
    finished = [e for e in service.events(job.id) if e.event_type == "environment_repair"
                and e.payload.get("phase") == "finished"][-1]
    assert finished.payload["outcome"] == "no_commands" and "changed nothing" in finished.reason


# ------------------------------------------------------------------ code overlay


def test_stage_containers_run_the_hosts_job_code(home, tmp_path):
    from mu.jobs.sandbox.build import code_overlay_mounts, repo_root
    from mu.jobs.sandbox.policy import SandboxPolicy, container_args
    service, job = _job(tmp_path)
    policy = SandboxPolicy.for_stage("repair", {})
    assert policy.code_overlay is True  # default on
    dirs = [tmp_path / d for d in ("in", "out", "relay")]
    for d in dirs:
        d.mkdir()
    args = container_args("c", "repair", job.worktree, *map(str, dirs), policy, "t", role="job-repair")
    mounts = [a for a in args if a.startswith("type=bind")]
    root = str(repo_root())
    assert f"type=bind,src={root}/mu,dst=/opt/mucli/mu,readonly" in mounts
    assert f"type=bind,src={root}/mucli.py,dst=/opt/mucli/mucli.py,readonly" in mounts
    assert dict(code_overlay_mounts())[f"{root}/providers"] == "/opt/mucli/providers"
    off = container_args("c", "checks", job.worktree, *map(str, dirs), replace(policy, code_overlay=False), "")
    assert not any("/opt/mucli" in a for a in off)


def test_code_overlay_is_skipped_when_the_tree_holds_a_denied_file(tmp_path):
    from mu.jobs.sandbox.build import code_overlay_mounts
    for name in ("mu", "providers", "utils"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "a.py").write_text("x = 1\n")
    (tmp_path / "mucli.py").write_text("")
    assert [dst for _src, dst in code_overlay_mounts(tmp_path)] == [
        "/opt/mucli/mu", "/opt/mucli/providers", "/opt/mucli/utils", "/opt/mucli/mucli.py"]
    (tmp_path / "utils" / ".env").write_text("SECRET=1\n")
    assert code_overlay_mounts(tmp_path) == []


def test_with_the_overlay_only_dependency_drift_marks_an_image_stale(home, tmp_path):
    from mu.jobs.sandbox.build import DEPS_LABEL, deps_digest
    from mu.jobs.sandbox.policy import SandboxPolicy
    from mu.jobs.sandbox.runner import check_runtime_image
    service, job = _job(tmp_path)
    policy = SandboxPolicy.for_stage("implement", {})

    def runner_for(labels):
        def run(args, check=True, timeout=None, **_kw):
            fmt = args[args.index("--format") + 1]
            return SimpleNamespace(returncode=0, stdout=next((v for k, v in labels.items() if k in fmt), ""), stderr="")
        return SimpleNamespace(run=run)
    old_code = {"io.mucli.source": "0000000000000000", DEPS_LABEL: deps_digest()}
    assert check_runtime_image(service, job, runner_for(old_code), "docker", policy) == ""
    assert check_runtime_image(service, job, runner_for({"io.mucli.source": "0000"}), "docker", policy) == ""
    stale = check_runtime_image(service, job, runner_for({DEPS_LABEL: "1111111111111111"}), "docker", policy)
    assert "older mucli dependencies" in stale
    # Without the overlay the code fingerprint still decides.
    assert "older mucli code" in check_runtime_image(service, job, runner_for(old_code), "docker",
                                                     replace(policy, code_overlay=False))


# ------------------------------------------------------------------ robustness


def test_repair_pins_its_base_image_against_a_concurrent_rebuild(home, tmp_path):
    """Setup → build the runtime image can move the base tag mid-repair; on the containerd image
    store the untagged base's content is then collected and `docker commit` fails. The repair
    holds its own tag on the base and reads the base config from it."""
    from mu.jobs.sandbox.repair import pin_tag, repair_and_recheck
    service, job = _job(tmp_path)
    run = _unrunnable_run(service, job)
    runner = RepairRunner()
    verified = _unrunnable_run(service, job, {"passed": True, "return_code": 0, "stdout": "1 passed"})
    repair_and_recheck(service, job.id, run, verify=lambda fresh: verified, runner=runner, relay_factory=FakeRelay,
                       settings={"sandbox": {"egress_extra": []}})
    pinned = pin_tag(job.id, 1)
    verbs = [c[1:] for c in runner.calls if c[1] in ("tag", "create", "commit", "rmi")]
    assert verbs[0] == ["tag", STATE["job_image"], pinned]
    assert pinned in runner.creates[0] and STATE["job_image"] not in runner.creates[0]
    assert [v[0] for v in verbs] == ["tag", "create", "commit", "rmi"] and verbs[-1] == ["rmi", pinned]
    inspected = [c for c in runner.calls if c[1] == "image" and "--format" in c]
    assert inspected and all(c[-1] == pinned for c in inspected if "Config" in " ".join(c))


def test_job_containers_never_write_core_dumps(home, tmp_path):
    from mu.jobs.sandbox.policy import SandboxPolicy, container_args
    service, job = _job(tmp_path)
    dirs = [tmp_path / d for d in ("in2", "out2", "relay2")]
    for d in dirs:
        d.mkdir()
    for stage in ("implement", "checks", "repair", "plan"):
        args = container_args("c", stage, job.worktree, *map(str, dirs), SandboxPolicy.for_stage(stage, {}), "t")
        assert args[args.index("--ulimit") + 1] == "core=0"
