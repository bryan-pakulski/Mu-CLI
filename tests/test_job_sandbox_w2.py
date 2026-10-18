"""Job sandbox hardening and cost accounting (job_mode_plan.md, W2)."""
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from providers.base import StreamEvent
from mu.jobs.core.artifacts import JobArtifacts
from mu.jobs.sandbox.docker_proxy import LABEL_SESSION, ProxyContext, decide
from mu.jobs.sandbox.model_relay import ModelRelay, RelayDenied, price_usd
from mu.jobs.sandbox.policy import SandboxGuardError, SandboxPolicy, container_args, repository_allow_paths, workspace_precheck


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "app.py").write_text("print('hi')\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


# ------------------------------------------------------------------ 1. read-only engine


def _ctx(tmp_path, **kwargs):
    worktree, cache = tmp_path / "wt", tmp_path / "cache"
    worktree.mkdir(exist_ok=True)
    cache.mkdir(exist_ok=True)
    return ProxyContext(job_id="job1", worktree=str(worktree), cache_dir=str(cache), **kwargs)


def _create(ctx, host):
    return decide("POST", "/containers/create", {}, {"Image": "x", "HostConfig": host}, ctx)


def test_readonly_proxy_forces_worktree_mounts_read_only(tmp_path):
    ctx = _ctx(tmp_path, readonly=True, session="s1",
               volume_device=lambda name: "/workspace/sub" if name == "wsvol" else None)
    decision = _create(ctx, {"Binds": ["/workspace:/src", "/workspace/a:/a:rw,z", "/cache:/c", "data:/data", "wsvol:/w"],
                             "Mounts": [{"Type": "bind", "Source": "/workspace", "Target": "/m"},
                                        {"Type": "volume", "Source": "wsvol", "Target": "/v"},
                                        {"Type": "volume", "Source": "plain", "Target": "/p"}],
                             "VolumesFrom": ["builder", "other:rw"]})
    assert decision.allow
    host = decision.body["HostConfig"]
    assert host["Binds"] == ["/workspace:/src:ro", "/workspace/a:/a:ro,z", "/cache:/c", "data:/data", "wsvol:/w:ro"]
    assert [m.get("ReadOnly", False) for m in host["Mounts"]] == [True, True, False]
    assert host["VolumesFrom"] == ["builder:ro", "other:ro"]
    assert decision.body["Labels"][LABEL_SESSION] == "s1" and decision.body["Labels"]["io.mucli.job"] == "job1"


def test_writable_proxy_keeps_implement_mounts(tmp_path):
    decision = _create(_ctx(tmp_path), {"Binds": ["/workspace:/src"], "VolumesFrom": ["builder"]})
    assert decision.body["HostConfig"]["Binds"] == ["/workspace:/src"]
    assert decision.body["HostConfig"]["VolumesFrom"] == ["builder"]
    assert LABEL_SESSION not in decision.body["Labels"]


def test_readonly_proxy_blocks_writes_through_foreign_containers_and_volumes(tmp_path):
    ctx = _ctx(tmp_path, readonly=True, session="s1", session_owned=lambda ref: ref == "mine")
    # A container implement left running (with a writable /workspace) is off limits for writes.
    for op in ("exec", "start", "attach", "archive", "restart", "update"):
        method = "PUT" if op == "archive" else "POST"
        assert not decide(method, f"/containers/old/{op}", {}, {}, ctx).allow, op
    for method, path in (("GET", "/containers/old/json"), ("GET", "/containers/old/logs"),
                         ("POST", "/containers/old/stop"), ("POST", "/containers/old/kill"),
                         ("DELETE", "/containers/old"), ("GET", "/containers/old/archive")):
        assert decide(method, path, {}, None, ctx).allow, path
    for op in ("start", "exec", "attach", "wait"):
        assert decide("POST", f"/containers/mine/{op}", {}, {}, ctx).allow
    assert not decide("POST", "/volumes/create", {}, {"DriverOpts": {"o": "bind", "device": "/workspace"}}, ctx).allow
    assert decide("POST", "/volumes/create", {}, {"DriverOpts": {"o": "bind", "device": "/cache/x"}}, ctx).allow
    assert not decide("POST", "/swarm/init", {}, None, ctx).allow
    assert not decide("POST", "/services/create", {}, None, ctx).allow
    writable = _ctx(tmp_path)
    assert decide("POST", "/containers/old/exec", {}, {}, writable).allow


def _inline_volume(options, driver=None, source="v"):
    config = {"Options": options}
    if driver is not None:
        config["Name"] = driver
    return {"Type": "volume", "Source": source, "Target": "/t", "VolumeOptions": {"DriverConfig": config}}


def test_proxy_refuses_inline_volume_driver_options_that_escape_the_mount_policy(tmp_path):
    # An inline DriverConfig creates the volume on the fly: it must obey /volumes/create's rules.
    for ctx in (_ctx(tmp_path), _ctx(tmp_path, readonly=True, session="s1")):
        for opts in ({"type": "none", "o": "bind", "device": "/run/mucli"},       # raw engine socket dir
                     {"type": "none", "o": "rbind", "device": "/run/mucli"},
                     {"type": "none", "o": "bind", "device": "/cache/../run/mucli"},
                     {"type": "none", "o": "bind", "device": "/"},
                     {"type": "nfs", "o": "addr=10.0.0.1", "device": ":/export"},
                     {"type": "ext4", "device": "/dev/sda1"},
                     {"type": "overlay", "o": "lowerdir=/run/mucli", "device": "overlay"},
                     {"o": "bind", "device": "relative"}):
            assert not _create(ctx, {"Mounts": [_inline_volume(opts)]}).allow, opts
            assert not decide("POST", "/volumes/create", {}, {"DriverOpts": opts}, ctx).allow, opts
        assert not _create(ctx, {"Mounts": [_inline_volume({}, driver="vieux/sshfs")]}).allow
        assert not decide("POST", "/volumes/create", {}, {"Driver": "rexray"}, ctx).allow
        assert _create(ctx, {"Mounts": [_inline_volume({"type": "tmpfs", "device": "tmpfs", "o": "size=10m"})]}).allow
        assert _create(ctx, {"Mounts": [_inline_volume({"type": "none", "o": "bind", "device": "/cache/x"})]}).allow
        assert _create(ctx, {"Mounts": [{"Type": "volume", "Source": "plain", "Target": "/p"}]}).allow
    # A read-only stage's inline bind of the worktree is forced read-only (never writable).
    ro = _create(_ctx(tmp_path, readonly=True, session="s1"),
                 {"Mounts": [_inline_volume({"type": "none", "o": "bind", "device": "/workspace"})]})
    assert ro.allow and ro.body["HostConfig"]["Mounts"][0]["ReadOnly"] is True
    assert not decide("POST", "/volumes/create", {}, {"DriverOpts": {"type": "none", "o": "rbind", "device": "/workspace"}},
                      _ctx(tmp_path, readonly=True)).allow


def test_plan_and_review_get_no_engine_and_checks_get_a_readonly_proxy(tmp_path, monkeypatch):
    from mu.jobs.sandbox import engine
    seen = []

    class FakeEngine:
        def __init__(self, *a, **k):
            pass

        def proxy(self, socket_path, *, egress, gpus, readonly=False):
            seen.append(readonly)
            return "proxy"
    monkeypatch.setattr(engine, "JobEngine", FakeEngine)
    state = {"name": "env", "docker": True}
    job = SimpleNamespace(id="job1", worktree=str(tmp_path))
    for stage in ("plan", "review"):
        policy, proxy = engine.attach_docker(SandboxPolicy(), job, state, [state], tmp_path, stage=stage)
        assert proxy is None and policy.docker_socket == ""
    assert seen == []
    _, proxy = engine.attach_docker(SandboxPolicy(), job, state, [state], tmp_path, stage="checks")
    engine.attach_docker(SandboxPolicy(), job, state, [state], tmp_path, stage="implement")
    assert proxy == "proxy" and seen == [True, False]


# ------------------------------------------------------------------ 2. cost


class Provider:
    model_name = ""

    def stream(self, messages, **kwargs):
        yield StreamEvent(kind="text_delta", text="hello")
        yield StreamEvent(kind="usage", input_tokens=1_000_000, output_tokens=0, total_tokens=1_000_000)


def test_stage_budget_counts_earlier_runs_of_the_same_stage(tmp_path):
    art = JobArtifacts("j", str(tmp_path)).ensure()
    art.append_cost({"stage": "implement", "cost_usd": 4.0})
    art.append_cost({"stage": "plan", "cost_usd": 3.0})
    relay = ModelRelay(SimpleNamespace(id="j", max_cost_usd=None), "implement", {"provider": "openai", "model": "gpt-5.6-sol"},
                       SandboxPolicy(max_cost_usd=5.0), art, provider_factory=lambda p, m: Provider())
    assert relay.policy.max_cost_usd == pytest.approx(1.0) and relay.prior_stage_cost == 4.0
    exhausted = ModelRelay(SimpleNamespace(id="j", max_cost_usd=None), "implement", {"provider": "openai", "model": "gpt-5.6-sol"},
                           SandboxPolicy(max_cost_usd=4.0), art, provider_factory=lambda p, m: Provider())
    with pytest.raises(RelayDenied, match="earlier implement runs"):
        list(exhausted.stream(exhausted.token, {"messages": []}))
    # The job cap counts the whole ledger.
    capped = ModelRelay(SimpleNamespace(id="j", max_cost_usd=8.0, cost_usd=0), "review", {"provider": "openai", "model": "gpt-5.6-sol"},
                        SandboxPolicy(max_cost_usd=50.0), art, provider_factory=lambda p, m: Provider())
    assert capped.policy.max_cost_usd == pytest.approx(1.0)


def test_unpriced_model_is_charged_a_fallback_price_and_warned_once(tmp_path):
    assert price_usd("ollama", "llama3", input_tokens=10**6) == (0.0, False)  # local Ollama is free
    cost, fallback = price_usd("openai", "no-such-model-xyz", input_tokens=10**6, output_tokens=10**6)
    assert fallback and cost == pytest.approx(90.0)
    art = JobArtifacts("j", str(tmp_path)).ensure()
    warnings = []
    relay = ModelRelay(SimpleNamespace(id="j"), "plan", {"provider": "openai", "model": "unpriced-model-w2"},
                       SandboxPolicy(), art, provider_factory=lambda p, m: Provider())
    relay.on_warning = warnings.append
    for _ in range(2):
        list(relay.stream(relay.token, {"messages": []}))
    assert relay.cost == pytest.approx(30.0)
    records = list(art.iter_costs())
    assert [r["cost_usd"] for r in records] == [pytest.approx(15.0)] * 2 and records[0]["fallback_price"]
    assert len(warnings) == 1 and "no price" in warnings[0]["message"]


def _raise_with_cost(exc, cost):
    exc.cost_usd = cost
    raise exc


@pytest.mark.parametrize("error,kind", [("paused", "needs_human"), ("failed", "failed"), ("timeout", "needs_human")])
def test_paused_failed_and_timed_out_runs_return_their_cost(monkeypatch, error, kind):
    from mu.jobs.sandbox import runner
    monkeypatch.setattr(runner, "implement_prompt", lambda service, job: "p")
    exc = {"paused": runner.StagePaused("question"), "failed": RuntimeError("container crashed"),
           "timeout": runner.StagePaused("implement sandbox reached its 60s time limit")}[error]
    monkeypatch.setattr(runner, "run_stage_in_container", lambda *a, **k: _raise_with_cost(exc, 1.25))
    outcome = runner.SandboxJobRunner(SimpleNamespace()).run(SimpleNamespace(id="j"), None)
    assert outcome.kind == kind and outcome.cost_usd == 1.25


def test_restarted_runs_keep_their_cost(monkeypatch):
    from mu.jobs.sandbox import runner
    calls = []

    def once(service, job, stage, prompt, **kwargs):
        calls.append(stage)
        if len(calls) == 1:
            _raise_with_cost(runner.StageRestart("restart"), 2.0)
        return {"kind": "completed", "cost_usd": 0.5}
    monkeypatch.setattr(runner, "_run_stage_once", once)
    monkeypatch.setattr(runner, "_mark_resumable", lambda *a: None)
    service = SimpleNamespace(store=SimpleNamespace(append_event=lambda *a, **k: None), get=lambda job_id: SimpleNamespace(id=job_id))
    assert runner.run_stage_in_container(service, SimpleNamespace(id="j"), "implement", "p")["cost_usd"] == 2.5


def test_job_cost_follows_the_ledger_even_when_plan_pauses(tmp_path):
    from mu.jobs import JobService, JobStore
    from mu.jobs.core.artifacts import artifacts_for
    from mu.jobs.sandbox.runner import StagePaused
    from mu.jobs.stages import run_plan_stage, sync_job_cost
    (tmp_path / "w").mkdir()
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create_from_payload({"title": "t", "workspace_root": str(tmp_path / "w")})

    def paused(job, prompt, workspace, model):
        artifacts_for(job).append_cost({"stage": "plan", "cost_usd": 0.75})
        raise StagePaused("needs input")
    with pytest.raises(StagePaused):
        run_plan_stage(service, job.id, generate_fn=paused)
    assert service.get(job.id).cost_usd == pytest.approx(0.75)
    artifacts_for(job).append_cost({"stage": "implement", "cost_usd": 1.0})
    assert sync_job_cost(service, job.id) == pytest.approx(1.75)
    assert sync_job_cost(service, job.id) == pytest.approx(1.75)  # idempotent
    assert service.get(job.id).cost_usd == pytest.approx(1.75)


def test_default_job_cap_is_100_and_zero_means_unlimited(tmp_path):
    from mu.jobs import JobService, JobStore
    from mu.jobs.job_settings import FIELD_BY_PATH, save_settings
    assert FIELD_BY_PATH["defaults.max_cost_usd"]["default"] == 100.0
    (tmp_path / "w").mkdir()
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    assert service.create_from_payload({"title": "t", "workspace_root": str(tmp_path / "w")}).max_cost_usd == 100.0
    assert service.create_from_payload({"title": "t", "workspace_root": str(tmp_path / "w"), "max_cost_usd": 0}).max_cost_usd is None
    save_settings({"defaults": {"max_cost_usd": 0}})
    assert service.create_from_payload({"title": "t", "workspace_root": str(tmp_path / "w")}).max_cost_usd is None


# ------------------------------------------------------------------ 3. workspace pre-check


def test_test_fixtures_pass_and_other_secret_files_need_the_base_commit_allow_list(tmp_path):
    repo = _repo(tmp_path)
    # Secret-looking files under a test/fixture directory are test data: allowed, tracked or not
    # (a job worktree is a fresh checkout, so they come from the repository or the agent).
    (repo / "tests" / "fixtures").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "server.key").write_text("-----BEGIN TEST KEY-----\n")
    (repo / "spec").mkdir()
    (repo / "spec" / "credentials.json").write_text("{}")  # untracked, agent-made
    _git(repo, "add", "tests")
    _git(repo, "commit", "-qm", "fixture")
    workspace_precheck(str(repo))
    # Elsewhere a tracked secret file still blocks until the base commit allow-lists it.
    (repo / "config").mkdir()
    (repo / "config" / "server.key").write_text("x")
    _git(repo, "add", "config")
    _git(repo, "commit", "-qm", "config key")
    with pytest.raises(SandboxGuardError, match="config/server.key"):
        workspace_precheck(str(repo))
    (repo / ".mucli").mkdir()
    (repo / ".mucli" / "jobs.toml").write_text('[sandbox]\nallow_paths = ["config/*.key"]\n')
    job = SimpleNamespace(worktree=str(repo), base_sha=_git(repo, "rev-parse", "HEAD"))
    assert repository_allow_paths(job) == ()  # not committed at the base: a stage cannot allow itself
    _git(repo, "add", ".mucli")
    _git(repo, "commit", "-qm", "allow")
    job.base_sha = _git(repo, "rev-parse", "HEAD")
    allow = repository_allow_paths(job)
    assert allow == ("config/*.key",)
    workspace_precheck(str(repo), allow_paths=allow)
    # Untracked secrets outside fixture directories are never allowed, even when a glob matches.
    (repo / "config" / "other.key").write_text("x")
    with pytest.raises(SandboxGuardError, match="other.key"):
        workspace_precheck(str(repo), allow_paths=allow)


def test_ignored_and_vendor_trees_with_absolute_symlinks_pass(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text(".venv/\n*.log\nlocal.pem\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ignore")
    bin_dir = repo / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    os.symlink("/usr/bin/python3", bin_dir / "python")
    (repo / ".venv" / "id_rsa").write_text("x")
    (repo / "local.pem").write_text("x")  # gitignored output
    nm = repo / "web" / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    os.symlink("/etc/hosts", nm / "link")  # untracked, not ignored, but a vendor tree
    workspace_precheck(str(repo))
    os.symlink("/etc/passwd", repo / "escape")
    with pytest.raises(ValueError, match="escapes"):
        workspace_precheck(str(repo))


def test_pids_limit_is_a_setting(tmp_path):
    assert SandboxPolicy.for_stage("implement", {"sandbox": {}}).pids_limit == 4096
    assert SandboxPolicy.for_stage("implement", {"sandbox": {"pids_limit": 10000}}).pids_limit == 10000
    ws = tmp_path / "ws"
    ws.mkdir()
    dirs = [tmp_path / n for n in ("i", "o", "r")]
    for d in dirs:
        d.mkdir()
    args = container_args("n", "plan", str(ws), *map(str, dirs), SandboxPolicy(pids_limit=5000), "t")
    assert args[args.index("--pids-limit") + 1] == "5000"


# ------------------------------------------------------------------ 4. guard


def _guard_job(repo, base):
    return SimpleNamespace(id="j", worktree=str(repo), base_sha=base, metadata={}, execution={})


def test_guard_fails_on_secrets_in_the_diff(tmp_path, monkeypatch):
    from mu.jobs.sandbox import runner
    monkeypatch.setattr("mu.jobs.core.plan_ops.approved_plan_text", lambda job: "")
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    job = _guard_job(repo, base)
    (repo / "app.py").write_text("print('changed')\n")
    runner._guard(job, SandboxPolicy())
    (repo / "config.py").write_text("TOKEN = 'opaque-credential-0123456789'\n")  # untracked file
    runner._guard(job, SandboxPolicy())
    with pytest.raises(runner.SandboxGuardError, match="config.py:1: injected credential"):
        runner._guard(job, SandboxPolicy(), literals=["opaque-credential-0123456789"])
    (repo / "config.py").unlink()
    (repo / "app.py").write_text("KEY = 'AKIAABCDEFGHIJKLMNOP'\n")
    _git(repo, "commit", "-qam", "committed leak")  # committed since base: still caught
    with pytest.raises(runner.SandboxGuardError, match="secrets"):
        runner._guard(job, SandboxPolicy())


def test_guard_scans_binary_diff_disabled_and_large_untracked_files(tmp_path, monkeypatch):
    from mu.jobs.sandbox import runner
    monkeypatch.setattr("mu.jobs.core.plan_ops.approved_plan_text", lambda job: "")
    repo = _repo(tmp_path)
    job = _guard_job(repo, _git(repo, "rev-parse", "HEAD"))
    # .gitattributes "-diff" would print "Binary files differ" without --text.
    (repo / ".gitattributes").write_text("app.py -diff\n")
    (repo / "app.py").write_text("KEY = 'AKIAABCDEFGHIJKLMNOP'\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "hide")
    with pytest.raises(runner.SandboxGuardError, match="app.py"):
        runner._guard(job, SandboxPolicy())
    _git(repo, "reset", "-q", "--hard", job.base_sha)
    # A binary (NUL-bearing) file carrying the injected credential.
    (repo / "blob.bin").write_bytes(b"\x00\x00 opaque-credential-0123456789 \x00")
    _git(repo, "add", "blob.bin")
    _git(repo, "commit", "-qm", "bin")
    with pytest.raises(runner.SandboxGuardError, match="blob.bin"):
        runner._guard(job, SandboxPolicy(), literals=["opaque-credential-0123456789"])
    _git(repo, "reset", "-q", "--hard", job.base_sha)
    # An untracked file above the old 2 MB cut-off is scanned, not skipped ...
    (repo / "big.txt").write_text("x" * (3 * 1024 * 1024) + "\nopaque-credential-0123456789\n")
    with pytest.raises(runner.SandboxGuardError, match="big.txt"):
        runner._guard(job, SandboxPolicy(), literals=["opaque-credential-0123456789"])
    # ... including far above the scan's chunk size: streamed in pieces, never refused or skipped.
    from mu.security.diff_scan import scan_patch
    pieces = list(runner.worktree_patches(job, chunk_bytes=1024))
    assert len(pieces) > 100
    found = [f for piece in pieces for f in scan_patch(piece, literals=["opaque-credential-0123456789"])]
    assert [(f.path, f.line) for f in found] == [("big.txt", 2)]


def test_guard_runs_the_workspace_precheck(tmp_path, monkeypatch):
    from mu.jobs.sandbox import runner
    monkeypatch.setattr("mu.jobs.core.plan_ops.approved_plan_text", lambda job: "")
    repo = _repo(tmp_path)
    job = _guard_job(repo, _git(repo, "rev-parse", "HEAD"))
    (repo / "server.pem").write_text("x")
    with pytest.raises(runner.SandboxGuardError, match="server.pem"):
        runner._guard(job, SandboxPolicy())


def test_injected_literals_cover_env_and_credential_files(monkeypatch):
    from mu.jobs import harness
    from mu.jobs.sandbox.runner import injected_literals
    monkeypatch.setattr(harness, "credentials", lambda agent, settings=None: {
        "env": {"ANTHROPIC_API_KEY": "sk-ant-xyz-1234567890"},
        "files": {".credentials.json": json.dumps({"claudeAiOauth": {"accessToken": "tok-abcdefghijkl", "expiresAt": 1}})}})
    assert set(injected_literals("claude")) >= {"sk-ant-xyz-1234567890", "tok-abcdefghijkl"}
    assert injected_literals("mucli") == []


# ------------------------------------------------------------------ 5. error class


@pytest.mark.parametrize("status,error,expected", [
    ("completed", "", ""), ("needs_input", "", ""), ("usage_limit", "", ""),
    ("max_iterations_reached", "", "iteration_limit"),
    ("error", "Stage model request failed (RateLimitError: 429 Too Many Requests)", "rate_limited"),
    ("error", "Stage model request failed (APIStatusError: 503 Service Unavailable)", "provider_transient"),
    ("error", "connection reset by peer", "provider_transient"),
    ("error", "Stage cost budget exhausted: $1.00 of $1.00", "fatal"),
    ("error", "429 insufficient_quota", "fatal"),
    # Overflow is still never a transient (backoff) retry; it is its own class, which the
    # sandbox runner retries once with a fresh, summary-seeded conversation.
    ("error", "prompt is too long: 300000 tokens", "context_overflow"),
    ("context_overflow", "", "context_overflow"),
    ("error", "KeyError: 'x'", "fatal"),
])
def test_error_classes(status, error, expected):
    from mu.jobs.sandbox.worker import classify_error
    assert classify_error(status, error) == expected


@pytest.mark.parametrize("error_class,transient", [("rate_limited", True), ("provider_transient", True),
                                                   ("iteration_limit", False), ("fatal", False)])
def test_runner_marks_transient_outcomes(monkeypatch, error_class, transient):
    from mu.jobs.sandbox import runner
    monkeypatch.setattr(runner, "implement_prompt", lambda service, job: "p")
    monkeypatch.setattr(runner, "run_stage_in_container", lambda *a, **k: {
        "kind": "needs_human", "status": "error", "error": "x", "cost_usd": 0.3, "error_class": error_class})
    outcome = runner.SandboxJobRunner(SimpleNamespace()).run(SimpleNamespace(id="j"), None)
    assert outcome.transient is transient and outcome.error_class == error_class and outcome.cost_usd == 0.3
    assert outcome.kind == ("failed" if transient else "needs_human")


# ------------------------------------------------------------------ 7. messages and stale image


def test_missing_image_points_to_setup_not_terminal_commands():
    from mu.container.docker_cli import ContainerRuntimeError
    from mu.jobs.sandbox.runner import SandboxUnavailable, require_image

    class Docker:
        def require(self, name):
            return "docker"

        def run(self, *a, **k):
            raise ContainerRuntimeError("no such image")
    for environment in (None, {"name": "rust"}):
        with pytest.raises(SandboxUnavailable) as info:
            require_image(Docker(), SandboxPolicy(), environment)
        text = str(info.value)
        assert "(E)" not in text and "python3 -m" not in text
    with pytest.raises(SandboxUnavailable, match="Open Setup"):
        require_image(Docker(), SandboxPolicy(), None)


def test_stale_runtime_image_warns_on_the_job_once(monkeypatch):
    from mu.jobs.sandbox import build, runner
    monkeypatch.setattr(build, "source_digest", lambda source=None: "new")
    events, meta = [], {}
    service = SimpleNamespace(get=lambda job_id: SimpleNamespace(metadata=dict(meta)),
                              store=SimpleNamespace(append_event=lambda *a, **k: events.append((a, k)),
                                                    update_runtime_fields=lambda job_id, metadata_json: meta.update(metadata_json)))

    class Docker:
        label = "old"

        def run(self, *a, **k):
            return SimpleNamespace(returncode=0, stdout=self.label + "\n")
    docker = Docker()
    job = SimpleNamespace(id="j")
    warning = runner.check_runtime_image(service, job, docker, "docker", SandboxPolicy())
    assert "older mucli code" in warning and "Open Setup" in warning
    runner.check_runtime_image(service, job, docker, "docker", SandboxPolicy())
    assert len(events) == 1 and meta["runtime_image_warning"] == warning
    docker.label = "new"
    assert runner.check_runtime_image(service, job, docker, "docker", SandboxPolicy()) == ""


def test_guard_allows_large_committed_files_and_still_scans_them(tmp_path, monkeypatch):
    """No changed-file size limit: a large file the agent produced (and a checkpoint committed)
    passes the guard; it leaves the in-memory diff and is streamed through the secret scan."""
    from mu.jobs.sandbox import runner
    from mu.security.diff_scan import scan_patch
    monkeypatch.setattr("mu.jobs.core.plan_ops.approved_plan_text", lambda job: "")
    repo = _repo(tmp_path)
    job = _guard_job(repo, _git(repo, "rev-parse", "HEAD"))
    (repo / "asset.bin").write_bytes(b"\x00" * (6 * 1024 * 1024))  # above SCAN_CHUNK_BYTES
    _git(repo, "add", "asset.bin")
    _git(repo, "commit", "-qm", "big asset")
    runner._guard(job, SandboxPolicy())
    diff = next(runner.worktree_patches(job))
    assert "asset.bin" not in diff  # streamed, not held in the diff
    (repo / "asset.bin").write_bytes(b"\x00" * (6 * 1024 * 1024) + b"\nKEY = 'AKIAABCDEFGHIJKLMNOP'\n")
    _git(repo, "commit", "-qam", "leak in the big file")
    with pytest.raises(runner.SandboxGuardError, match="asset.bin"):
        runner._guard(job, SandboxPolicy())
    assert any(f.path == "asset.bin" for p in runner.worktree_patches(job) for f in scan_patch(p))
