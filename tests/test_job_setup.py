"""Jobs Setup checklist and Doctor (mu/jobs/setup.py), the runtime image fingerprint and the
sandbox probe contract — without Docker (step checks that need it are stubbed)."""
import json
import os
import stat
import threading
import time

import pytest

from mu.jobs import setup as jobs_setup
from mu.jobs.setup import StepStatus


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    for key in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    jobs_setup._jira_ok_for.update(sig="")
    return tmp_path


@pytest.fixture
def stub_checks(monkeypatch):
    """Every step ok unless overridden; real check() for the steps a test names."""
    states = {s: StepStatus("ok", f"{s} fine") for s in jobs_setup.STEP_IDS}
    real = jobs_setup.check

    def fake(step):
        value = states[step]
        return real(step) if value == "real" else value
    monkeypatch.setattr(jobs_setup, "check", fake)
    return states


def fresh():
    jobs_setup._setup = None
    return jobs_setup.get_setup()


def test_checklist_shape_blocking_and_finish(stub_checks):
    stub_checks["runtime"] = StepStatus("todo", "not built")
    stub_checks["claude"] = StepStatus("todo", "not installed")  # optional: never blocks
    s = fresh()
    s.recheck()
    vm = s.vm()
    assert [st["id"] for st in vm["steps"]] == jobs_setup.STEP_IDS
    assert vm["all_checked"] and vm["blocking"] == ["Job runtime image"] and not vm["can_finish"]
    runtime = next(st for st in vm["steps"] if st["id"] == "runtime")
    assert runtime["actions"][0]["kind"] == "job" and runtime["why"]
    with pytest.raises(ValueError, match="Job runtime image"):
        s.finish()
    stub_checks["runtime"] = StepStatus("ok", "built")
    s.recheck(["runtime"])
    assert s.finish()["completed_at"] and not jobs_setup.needs_setup()


def test_optional_steps_can_be_skipped_and_unskipped(stub_checks):
    stub_checks["jira"] = StepStatus("optional", "not configured")
    s = fresh()
    s.recheck()
    s.action("jira", "skip")
    jira = next(st for st in s.vm()["steps"] if st["id"] == "jira")
    assert jira["state"] == "skipped" and jira["detail"].startswith("skipped")
    assert [a["id"] for a in jira["actions"]][-1] == "unskip"
    s.action("jira", "unskip")
    assert next(st for st in s.vm()["steps"] if st["id"] == "jira")["state"] == "optional"
    with pytest.raises(ValueError):
        s.action("docker", "skip")  # required steps can't be skipped


def test_actions_are_validated(stub_checks):
    s = fresh()
    s.recheck()
    with pytest.raises(ValueError, match="unknown setup step"):
        s.action("nope", "x")
    with pytest.raises(ValueError, match="no action"):
        s.action("git", "build")
    with pytest.raises(ValueError, match="2 answers"):
        s.action("git", "identity", ["only name"])
    assert s.action("models", "settings") == {"ok": True, "nav": "settings"}


def test_git_identity_action_writes_the_global_config(stub_checks, home):
    stub_checks["git"] = "real"
    s = fresh()
    s.recheck(["git"])
    assert next(st for st in s.vm()["steps"] if st["id"] == "git")["state"] == "todo"
    s.action("git", "identity", ["Ada Lovelace", "ada@example.com"])
    assert "Ada Lovelace" in (home / "gitconfig").read_text()
    git = next(st for st in s.vm()["steps"] if st["id"] == "git")
    assert git["state"] == "ok" and git["detail"] == "Ada Lovelace <ada@example.com>"
    with pytest.raises(ValueError):
        s.action("git", "identity", ["", ""])


def test_models_agents_and_secrets_actions(stub_checks):
    from mu.jobs import harness, job_settings
    stub_checks["models"] = "real"
    stub_checks["agents"] = "real"
    s = fresh()
    s.recheck()
    assert next(st for st in s.vm()["steps"] if st["id"] == "models")["state"] == "todo"
    s.action("models", "all-stages", ["anthropic", "claude-sonnet-5-5"])
    assert job_settings.missing_stages({}) == []
    s.action("agents", "defaults", ["plan=claude,implement=mucli,review=codex"])
    assert harness.status()["defaults"] == {"plan": "claude", "implement": "mucli", "review": "codex"}
    agents = next(st for st in s.vm()["steps"] if st["id"] == "agents")
    assert agents["state"] == "todo" and "not ready" in agents["detail"]  # neither CLI is set up here
    with pytest.raises(ValueError, match="missing"):
        s.action("agents", "defaults", ["plan=claude"])
    s.action("claude", "paste-token", ["sk-ant-oat-secret-value"])
    path = harness.secrets_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert "secret-value" not in json.dumps(s.vm())
    s.action("codex", "paste-key", ["sk-openai-secret"])
    assert harness.read_secrets()["OPENAI_API_KEY"] == "sk-openai-secret"


def test_job_actions_stream_a_log_and_run_one_at_a_time(stub_checks, monkeypatch):
    gate = threading.Event()

    def selftest(log):
        log("mucli · plan")
        gate.wait(5)
        log("PASS: 18/18 checks")
        return True
    monkeypatch.setattr(jobs_setup, "run_sandbox_selftest", selftest)
    s = fresh()
    s.recheck()
    seen = []
    started = s.action("sandbox", "selftest", on_line=seen.append)
    assert started["job"]["running"]
    with pytest.raises(RuntimeError, match="still running"):
        s.action("sandbox", "selftest")
    gate.set()
    for _ in range(100):
        if not s.vm()["job"]["running"]:
            break
        time.sleep(0.05)
    job = s.vm()["job"]
    assert job["ok"] and job["lines"] == ["mucli · plan", "PASS: 18/18 checks"] and seen == job["lines"]


def test_runtime_image_state_follows_the_code_fingerprint(monkeypatch):
    from mu.jobs.sandbox import build
    monkeypatch.setattr(build, "source_digest", lambda source=None: "abc")
    for label, state, words in ((None, "todo", "not built"), ("", "todo", "before images carried"),
                                ("old", "todo", "older mucli code"), ("abc", "ok", "code abc")):
        monkeypatch.setattr(build, "image_digest", lambda tag=build.IMAGE, label=label: label)
        status = jobs_setup.check("runtime")
        assert status.state == state and words in status.detail


def test_sandbox_state_requires_a_fresh_passing_selftest(monkeypatch):
    from mu.jobs.sandbox import build
    monkeypatch.setattr(build, "image_digest", lambda tag=build.IMAGE: "img-1")
    assert jobs_setup.check("sandbox").state == "todo"
    jobs_setup.write_setup(selftest={"at": time.time(), "ok": True, "image": "img-1", "summary": "108/108 checks"})
    assert jobs_setup.check("sandbox").state == "ok"
    monkeypatch.setattr(build, "image_digest", lambda tag=build.IMAGE: "img-2")
    assert "changed since" in jobs_setup.check("sandbox").detail
    jobs_setup.write_setup(selftest={"at": time.time(), "ok": False, "image": "img-2", "failures": ["mucli plan: x"]})
    assert jobs_setup.check("sandbox").detail.startswith("FAILED")


def test_doctor_levels_and_deep_selftest(stub_checks, monkeypatch):
    stub_checks["runtime"] = StepStatus("todo", "stale")
    stub_checks["dind"] = StepStatus("optional", "no sysbox")
    calls = []
    monkeypatch.setattr(jobs_setup, "run_sandbox_selftest", lambda log: calls.append(1) or log("PASS: 1/1 checks") or True)
    from mu.jobs import job_settings
    job_settings.save_settings({"harness": {"claude": {"binary": "/nonexistent/claude"}}})
    checks = {c["name"]: c for c in jobs_setup.doctor()}
    assert checks["runtime_image"]["status"] == "fail" and checks["docker_in_docker"]["status"] == "warn"
    assert checks["docker"]["status"] == "ok" and checks["claude_binary"]["status"] == "warn"  # unused + missing
    assert checks["sandbox_selftest"]["detail"] == "sandbox fine" and not calls
    job_settings.save_settings({"harness": {"default": {"plan": "claude"}}})
    assert {c["name"]: c for c in jobs_setup.doctor()}["claude_binary"]["status"] == "fail"  # a default agent
    deep = {c["name"]: c for c in jobs_setup.doctor(deep=True)}
    assert calls and deep["sandbox_selftest"] == {"name": "sandbox_selftest", "status": "ok", "detail": "PASS: 1/1 checks"}


def test_a_crashing_check_reports_instead_of_raising(monkeypatch):
    monkeypatch.setattr(jobs_setup, "check", lambda step: (_ for _ in ()).throw(RuntimeError("boom")))
    assert jobs_setup.check_step("docker") == StepStatus("warn", "check failed: boom")


def test_source_digest_tracks_executed_code_only(tmp_path):
    from mu.jobs.sandbox.build import source_digest
    for rel, text in (("mu/a.py", "x = 1"), ("mu/gui/static/app.js", "1"), ("prompts/p.md", "hi"), ("mucli.py", "")):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    first = source_digest(tmp_path)
    (tmp_path / "mu/gui/static/app.js").write_text("2")
    assert source_digest(tmp_path) == first  # web assets never run in a stage
    (tmp_path / "prompts/p.md").write_text("changed")
    second = source_digest(tmp_path)
    assert second != first
    (tmp_path / "mu/a.py").write_text("x = 2")
    assert source_digest(tmp_path) not in (first, second)


GOOD_PROBE = {"uid": os.getuid(), "gid": os.getgid(), "cap_eff": "0000000000000000", "cap_prm": "0000000000000000",
              "no_new_privs": "1", "seccomp": "2", "write_rootfs": False, "write_etc": False, "write_tmp": True,
              "write_workspace": False, "write_artifacts": False, "write_relay": False, "write_creds": False,
              "write_claude_bin": False, "write_codex_bin": False, "write_git_pointer": False, "ifaces": ["lo"],
              "direct_internet": False, "dns": False, "proxy:example.com": "HTTP/1.1 403 Forbidden",
              "proxy:api.anthropic.com": "HTTP/1.1 200 Connection Established",
              "proxy:api.openai.com": "HTTP/1.1 403 Forbidden", "proxy:chatgpt.com": "HTTP/1.1 403 Forbidden",
              "docker_sock": False, "secret_env": ["ANTHROPIC_API_KEY"], "cred_modes": ["0o600"]}


def test_probe_contract_flags_each_violation():
    from mu.jobs.sandbox.selftest import check_probe, parse_probe
    assert all(c.ok for c in check_probe(GOOD_PROBE, "claude", "plan"))
    bad = dict(GOOD_PROBE, cap_eff="00000000a80425fb", write_workspace=True, ifaces=["eth0", "lo"],
               **{"proxy:example.com": "HTTP/1.1 200 Connection Established"}, secret_env=["ANTHROPIC_API_KEY", "OPENAI_API_KEY"])
    failed = {c.name for c in check_probe(bad, "claude", "plan") if not c.ok}
    assert failed == {"no Linux capabilities", "workspace read-only", "no network interfaces besides loopback",
                      "egress blocks example.com", "only the agent's own credential is visible"}
    # mucli gets no egress at all; implement may write the workspace.
    mucli = dict(GOOD_PROBE, write_workspace=True, secret_env=[], cred_modes=[],
                 **{"proxy:api.anthropic.com": "refused: [Errno 111]"})
    assert all(c.ok for c in check_probe(mucli, "mucli", "implement"))
    assert parse_probe("noise\nPROBE:{\"a\": 1}\n") == {"a": 1} and parse_probe("PROBE:{trunc") is None


def test_setup_api_endpoints(stub_checks, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mu.gui.routers.jobs import router
    jobs_setup._setup = None
    app = FastAPI()
    app.include_router(router, prefix="/api/jobs")
    client = TestClient(app)
    vm = client.get("/api/jobs/setup").json()
    assert vm["total"] == len(jobs_setup.STEP_IDS) and vm["can_finish"]
    bad = client.post("/api/jobs/setup/action", json={"step": "git", "action": "nope"})
    assert bad.status_code == 400 and "no action" in bad.json()["detail"]
    saved = client.post("/api/jobs/setup/action", json={"step": "claude", "action": "paste-token", "inputs": ["sk-ant-oat-zzz-hidden"]})
    assert saved.status_code == 200 and "zzz-hidden" not in saved.text and saved.json()["setup"]["steps"]
    assert client.post("/api/jobs/setup/finish").json()["completed_at"]
    doctor = client.get("/api/jobs/doctor").json()
    assert doctor["checks"] and doctor["needs_setup"] is False and isinstance(doctor["failed"], int)
