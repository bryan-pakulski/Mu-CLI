"""Job build environments (documentation/plans/job_build_environments.md): resolution, selection,
home filtering, probe parsing, secret scan, egress, cleanup/sweeper, proxy policy and shapes."""
import io
import json
import tarfile
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from mu.jobs.checks import Suite
from mu.jobs.sandbox import environments as envs
from mu.jobs.sandbox.environments import (Binding, EnvironmentBlocked, binding_for_path, binding_for_suite,
                                          filter_home_tar, filter_model_hosts, home_excluded, image_secret_reason,
                                          parse_probe, parse_spec, primary_binding, resolve_bindings)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    import utils.config as config
    monkeypatch.setattr(config, "HISTORY_DIR", str(tmp_path / "home"))
    return tmp_path


def job(repo, execution=None, **meta):
    return SimpleNamespace(id="abcdef0123456789abcd", repository=str(repo), metadata={"repository_root": str(repo), **meta},
                           execution=execution or {})


def write_toml(repo, text):
    (repo / ".mucli").mkdir(parents=True, exist_ok=True)
    (repo / ".mucli" / "jobs.toml").write_text(text)


def save_session(home, name, container):
    path = home / "home" / "sessions" / name
    path.mkdir(parents=True)
    (path / "session.json").write_text(json.dumps({"container_config": {"container_name": container}}))


# ------------------------------------------------------------------ resolution


def test_precedence_job_then_repo_then_session_then_settings_then_default(home):
    repo = home / "rsq-cloud"
    repo.mkdir()
    save_session(home, "rsq", "mucli-rust-build")
    from mu.jobs.job_settings import save_settings
    save_settings({"sandbox": {"toolchains": [{"repository": str(repo), "base_image": "rust:1.89"}]}})
    write_toml(repo, '[[environments]]\nname = "node-build"\npaths = ["resq-frontend"]\n')

    assert resolve_bindings(job(repo, {"environments": ["template:trt:inference-backend-rs"]}, origin_session="rsq"))[1] == "job"
    bindings, source = resolve_bindings(job(repo, origin_session="rsq"))
    assert source == "repo" and bindings[0].ref == "node-build" and bindings[0].paths == ("resq-frontend",)
    (repo / ".mucli" / "jobs.toml").unlink()
    bindings, source = resolve_bindings(job(repo, origin_session="rsq"))
    assert source == "session" and bindings == [Binding("rust-build", "environment", "mucli-rust-build")]
    save_settings({"environments": {"use_session_environment": False}})
    assert resolve_bindings(job(repo, origin_session="rsq"))[1] == "settings"
    save_settings({"sandbox": {"toolchains": None}})
    assert resolve_bindings(job(repo, origin_session="rsq")) == ([], "default")


def test_jobs_toml_environments_parse_all_fields(home):
    repo = home / "repo"
    repo.mkdir()
    write_toml(repo, """
[[environments]]
name = "rust-build"
paths = ["resq-backend", "./stream-processor-rs/", "resqvision-core"]
primary = true

[[environments]]
name = "image:node:22-bookworm"
paths = ["resq-frontend"]

[[environments]]
name = "template:compose-it"
paths = ["."]
docker = true
gpus = "0,1"
preload_images = ["postgres:16"]
""")
    bindings, source = resolve_bindings(job(repo))
    assert source == "repo"
    rust, node, compose = bindings
    assert rust.paths == ("resq-backend", "stream-processor-rs", "resqvision-core") and rust.primary
    assert (node.name, node.source, node.ref) == ("node", "image", "node:22-bookworm")
    assert (compose.source, compose.paths, compose.docker, compose.gpus, compose.preload_images) == \
        ("template", (), True, "device=0,1", ("postgres:16",))
    assert rust.gpus is None  # inherits from the source environment


def test_invalid_bindings_are_rejected(home):
    with pytest.raises(ValueError, match="repository-relative"):
        Binding.from_dict({"env": "rust", "paths": ["../escape"]})
    with pytest.raises(ValueError, match="invalid image"):
        Binding.from_dict({"env": "image:bad image"})
    with pytest.raises(ValueError, match="needs a name"):
        Binding.from_dict({"paths": ["x"]})
    repo = home / "bad"
    repo.mkdir()
    write_toml(repo, "[[environments]\n")
    with pytest.raises(ValueError, match="not valid TOML"):
        resolve_bindings(job(repo))


def test_cli_specs(home):
    assert parse_spec("rust-build:resq-backend,stream-processor-rs") == Binding(
        "rust-build", "environment", "rust-build", ("resq-backend", "stream-processor-rs"))
    assert parse_spec("compose-it:.:docker").docker and parse_spec("compose-it:.:docker").paths == ()
    image = parse_spec("image:rust:1.89-bookworm:paths=backend:primary")
    assert (image.source, image.ref, image.paths, image.primary) == ("image", "rust:1.89-bookworm", ("backend",), True)
    template = parse_spec("template:trt:inference-backend-rs:gpus=all")
    assert (template.source, template.ref, template.paths, template.gpus) == ("template", "trt", ("inference-backend-rs",), "all")
    names = [b.name for b in envs.bindings_from_list(["rust", "rust"])]
    assert names == ["rust", "rust-2"]


# ------------------------------------------------------------------ selection


BINDINGS = [{"name": "rust-build", "paths": ["resq-backend", "stream-processor-rs"]},
            {"name": "node-build", "paths": ["resq-frontend"]},
            {"name": "compose-it", "paths": []}]


def test_binding_for_path_prefers_longest_root_then_whole_repo():
    assert binding_for_path(BINDINGS, "resq-frontend/src/app.ts")["name"] == "node-build"
    assert binding_for_path(BINDINGS, "stream-processor-rs")["name"] == "rust-build"
    assert binding_for_path(BINDINGS, "docs/readme.md")["name"] == "compose-it"
    assert binding_for_path(BINDINGS, "resq-frontend-old/x", fallback=False) is None


def test_primary_binding_marked_or_most_planned_files():
    assert primary_binding(BINDINGS, ["resq-frontend/a.ts", "resq-frontend/b.ts", "resq-backend/x.rs"])["name"] == "node-build"
    assert primary_binding(BINDINGS, [])["name"] == "compose-it"  # no plan yet: the whole-repo binding
    marked = [dict(BINDINGS[0]), dict(BINDINGS[1], primary=True)]
    assert primary_binding(marked, ["resq-backend/x.rs"])["name"] == "node-build"
    assert primary_binding([], []) is None


def test_suite_binding_by_env_cwd_command_path_then_primary():
    assert binding_for_suite(BINDINGS, Suite("fe", "npm test", env="node-build"))["name"] == "node-build"
    assert binding_for_suite(BINDINGS, Suite("rs", "cargo test", cwd="stream-processor-rs"))["name"] == "rust-build"
    assert binding_for_suite(BINDINGS, Suite("rs", "cargo test --manifest-path=resq-backend/Cargo.toml"))["name"] == "rust-build"
    assert binding_for_suite(BINDINGS, Suite("it", "pytest -q"), ["resq-frontend/x"])["name"] == "node-build"
    with pytest.raises(ValueError, match="names build environment `nope`"):
        binding_for_suite(BINDINGS, Suite("x", "make", env="nope"))


def test_plan_checks_carry_env_and_cwd():
    from mu.jobs.core.plan_doc import PlanDoc
    doc = PlanDoc.parse("## Checks\n- suite: fe   env: node-build   cwd: resq-frontend   cmd: npm test\n- suite: unit   cmd: pytest -q\n")
    assert [(c.suite, c.env, c.cwd, c.cmd) for c in doc.checks] == [
        ("fe", "node-build", "resq-frontend", "npm test"), ("unit", "", "", "pytest -q")]
    assert PlanDoc.parse(doc.render()).checks == doc.checks


# ------------------------------------------------------------------ home, probe, secrets, egress


def _tar(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in entries:
            if data is None:
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


def test_home_tar_filter_drops_secrets_state_history_caches_and_extras():
    src = _tar([("./.rustup", None), ("./.rustup/settings.toml", b"x" * 10), ("./.cargo/bin/cargo", b"bin"),
                ("./.ssh", None), ("./.ssh/id_test", b"KEY"), ("./.bash_history", b"ls"), ("./.cache", None),
                ("./.cache/pip/x", b"c"), ("./.cargo/registry/index", b"i"), ("./.mucli/sessions", b"s"),
                ("./.bashrc", b"export TOKEN=1"), ("./work/server.pem", b"pem"), ("./notes/private.txt", b"n"),
                ("./.config/gh/hosts.yml", b"tok")])
    dst = io.BytesIO()
    kept, skipped = filter_home_tar(src, dst, ["notes"])
    dst.seek(0)
    names = sorted(tarfile.open(fileobj=dst).getnames())
    assert names == [".cargo/bin/cargo", ".rustup", ".rustup/settings.toml"]
    assert kept == 13
    joined = " ".join(skipped)
    for expected in (".ssh (secret)", ".bash_history (shell history)", ".cache (cache)", ".mucli/sessions (mucli state)",
                     ".bashrc (shell start-up file)", "server.pem", "notes/private.txt (excluded in Settings)"):
        assert expected in joined


def test_template_capture_keeps_caches_and_rc_files():
    assert home_excluded(".cache/pip", strict=False) is None and home_excluded(".bashrc", strict=False) is None
    assert home_excluded(".ssh/id_rsa", strict=False) and home_excluded(".zsh_history", strict=False)


def test_probe_parsing_keeps_path_and_toolchain_homes_only():
    raw = "PATH=/root/.cargo/bin:/usr/bin\0RUSTUP_HOME=/root/.rustup\0SECRET_TOKEN=x\0NVM_DIR=/root/.nvm\0WEIRD=a\"b\0"
    assert parse_probe(raw, envs.PROBE_KEYS) == {"PATH": "/root/.cargo/bin:/usr/bin", "RUSTUP_HOME": "/root/.rustup",
                                                 "NVM_DIR": "/root/.nvm"}
    assert parse_probe("PATH=/a\nMY_SDK=/opt/sdk\n", ["MY_SDK"], nul=False) == {"PATH": "/a", "MY_SDK": "/opt/sdk"}


def test_secret_reasons_ignore_trust_stores_and_package_trees():
    for path in ("/root/.ssh/id_test", "/home/dev/.aws/credentials", "/root/.netrc", "/etc/ssh/ssh_host_ed25519_key",
                 "/root/project/.env", "/root/keys/server.key", "/root/.zsh_history"):
        assert image_secret_reason(path), path
    for path in ("/etc/ssl/certs/ca.pem", "/root/.local/lib/python3.12/site-packages/certifi/cacert.pem",
                 "/etc/ssh/ssh_config", "/etc/ssh/ssh_config.d", "/etc/ssh/sshd_config.d",
                 "/root/.rustup/toolchains/x/test.pem", "/root/app/.env.example",
                 "/root/.cargo/bin/cargo", "/etc/ssh/ssh_host_ed25519_key.pub"):
        assert image_secret_reason(path) is None, path
    assert image_secret_reason("/root/keys/server.key", ["/root/keys/*.key"]) is None


class FakeDocker:
    """Answers docker CLI calls from a handler; records every call."""

    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or (lambda args: "")
        self.runner = SimpleNamespace(commands=[], require=lambda name: "/usr/bin/docker")

    binary = "/usr/bin/docker"

    def run(self, *args, check=True, timeout=None, input_text=None):
        self.calls.append(list(args))
        out = self.handler(list(args))
        if isinstance(out, tuple):
            code, text = out
        else:
            code, text = 0, out
        return SimpleNamespace(returncode=code, stdout=text or "", stderr="" if code == 0 else "boom")

    def exists(self, kind, ref):
        return self.run(kind, "inspect", ref).returncode == 0

    def inspect(self, kind, ref, fmt):
        return (self.run(kind, "inspect", "--format", fmt, ref).stdout or "").strip()


def test_secret_scan_lists_hits_from_the_job_image():
    docker = FakeDocker(lambda args: "/root\n/root/.ssh\n/root/.ssh/id_test\n/etc/ssl/certs/x.pem\n/root/.cargo/bin/cargo\n")
    hits = envs.secret_scan(docker, "img")
    assert hits == ["/root/.ssh (credentials directory ~/.ssh)", "/root/.ssh/id_test (credentials directory ~/.ssh)"]
    assert "-prune" in docker.calls[0][-1] and "/root /home /etc" in docker.calls[0][-1]


def test_egress_inheritance_drops_model_provider_hosts(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://gpu-box.lan:11434")
    assert filter_model_hosts(["crates.io", "api.openai.com", "openai.com", "*.anthropic.com", "*.googleapis.com",
                               "storage.googleapis.com", "gpu-box.lan", "pypi.org", "pypi.org"]) == \
        ["crates.io", "storage.googleapis.com", "pypi.org"]


def test_gpus_argument_construction():
    from mu.jobs.sandbox.policy import SandboxPolicy, container_args, gpus_arg
    assert gpus_arg("all") == "all" and gpus_arg("device=0") == "device=0" and gpus_arg("device=0,1") == '"device=0,1"'
    assert envs.normalize_gpus("0,1") == "device=0,1" and envs.normalize_gpus("inherit") is None and envs.normalize_gpus("") == ""


def test_container_args_label_job_and_add_gpus_and_docker_socket(home):
    from mu.jobs.sandbox.policy import SandboxPolicy, container_args
    dirs = [home / n for n in ("ws", "in", "out", "relay")]
    for d in dirs:
        d.mkdir()
    sock = dirs[3] / "docker.sock"
    sock.write_text("")
    policy = SandboxPolicy(job_id="abc123", binding="rust-build", gpus="device=0,1", docker_socket=str(sock))
    args = container_args("n", "checks", *map(str, dirs), policy, "")
    rendered = " ".join(args)
    assert "io.mucli.job=abc123" in rendered and f"io.mucli.home={envs.home_id()}" in rendered
    assert '--gpus "device=0,1"' in rendered and "dst=/var/run/docker.sock" in rendered
    assert "DOCKER_HOST=unix:///var/run/docker.sock" in rendered and "--network none" in rendered
    plain = " ".join(container_args("n", "checks", *map(str, dirs), SandboxPolicy(), ""))
    assert "--gpus" not in plain and "docker.sock" not in plain


# ------------------------------------------------------------------ lifecycle: ensure, cleanup, sweeper


def _service(tmp_path):
    from job_helpers import running_job
    service, _, job_row, _, repo = running_job(tmp_path)
    return service, job_row, repo


def test_ensure_resolves_once_and_reuses_ready_images(home, monkeypatch):
    service, row, repo = _service(home)
    service.store.update_runtime_fields(row.id, execution_json={**row.execution, "environments": ["image:rust:1.89"]})
    built = []

    def fake_snapshot(job, binding, *, docker, settings, log):
        built.append(binding["name"])
        return {**binding, "status": "ready", "job_image": f"mucli/job-x-{binding['name']}-runtime:1", "image_bytes": 7,
                "egress_allow": [], "gpus": ""}
    monkeypatch.setattr(envs, "snapshot_binding", fake_snapshot)
    monkeypatch.setattr(envs, "resolve_source", lambda b: {"image": b["ref"], "egress_allow": [], "gpus": ""})
    docker = FakeDocker()
    states = envs.ensure_environments(service, service.get(row.id), docker=docker, settings={})
    assert [s["name"] for s in states] == ["rust"] and built == ["rust"]
    again = envs.ensure_environments(service, service.get(row.id), docker=docker, settings={})
    assert again == states and built == ["rust"]  # images exist: no second snapshot
    meta = service.get(row.id).metadata
    assert meta["build_environments_source"] == "job" and meta["build_environments"][0]["status"] == "ready"
    kinds = [e.event_type for e in service.store.recent_events(row.id)]
    assert "build_environments_preparing" in kinds and "build_environment_ready" in kinds

    # Images cleaned while idle → re-created with a notice.
    gone = FakeDocker(lambda args: (1, "") if args[:2] == ["image", "inspect"] else "")
    envs.ensure_environments(service, service.get(row.id), docker=gone, settings={})
    assert built == ["rust", "rust"]
    assert any(e.event_type == "build_environments_recreated" for e in service.store.recent_events(row.id))


def test_ensure_parks_on_missing_environment_and_sysbox(home, monkeypatch):
    service, row, repo = _service(home)
    service.store.update_runtime_fields(row.id, execution_json={**row.execution, "environments": ["gone-env"]})
    with pytest.raises(EnvironmentBlocked, match="Build environment `gone-env` .* no longer exists; choose another"):
        envs.ensure_environments(service, service.get(row.id), docker=FakeDocker(), settings={})
    assert service.get(row.id).metadata["build_environments"][0]["status"] == "failed"

    service.store.update_runtime_fields(row.id, execution_json={**row.execution, "environments": ["image:rust:1.89:docker"]})
    monkeypatch.setattr(envs, "resolve_source", lambda b: {"image": b["ref"], "egress_allow": [], "gpus": ""})
    monkeypatch.setattr(envs, "sysbox_available", lambda docker=None: False)
    with pytest.raises(EnvironmentBlocked, match="requires Sysbox"):
        envs.ensure_environments(service, service.get(row.id), docker=FakeDocker(), settings={})


def test_default_runtime_when_nothing_resolves(home):
    service, row, repo = _service(home)
    assert envs.ensure_environments(service, service.get(row.id), docker=FakeDocker(), settings={}) == []
    assert service.get(row.id).metadata["build_environments_source"] == "default"


def test_cleanup_removes_everything_labelled_with_the_job():
    def handler(args):
        if args[:2] == ["ps", "-aq"]:
            return "c1\n" if "label=" in args[3] else "c2\n"
        if args[:3] == ["volume", "ls", "-q"]:
            return "mucli-job-abc-docker\n"
        if args[:3] == ["network", "ls", "-q"]:
            return "n1\n"
        if args[:3] == ["image", "ls", "-q"]:
            return "sha256:snap\nsha256:job\n"
        if args[:2] == ["image", "inspect"]:
            return "job-image" if args[-1] == "sha256:job" else "job-snapshot"
        return ""
    docker = FakeDocker(handler)
    report = envs.cleanup_job("abcdef0123456789", docker=docker)
    removed = [c for c in docker.calls if c[0] in ("rm", "volume", "network", "image") and c[1] in ("-f", "rm")]
    assert ["rm", "-f", "c1"] in removed and ["rm", "-f", "c2"] in removed
    assert ["volume", "rm", "-f", "mucli-job-abc-docker"] in removed and ["network", "rm", "n1"] in removed
    image_rms = [c[-1] for c in docker.calls if c[:3] == ["image", "rm", "-f"]]
    assert image_rms == ["sha256:job", "sha256:snap"]  # children first
    assert report["errors"] == [] and len(report["images"]) == 2


def test_sweep_decisions_terminal_archived_pushed_idle_and_kept(home):
    from mu.jobs.core.states import CoreState, JobState
    service, row, repo = _service(home)
    now = time.time()
    decide = lambda state, kept=False, archived=False, idle_h=0: envs.sweep_decision(
        SimpleNamespace(store=SimpleNamespace(list_events_by_type=lambda *a: [SimpleNamespace(created_at=now - idle_h * 3600)])),
        replace(row, metadata={"environments_kept": kept}), CoreState(state), archived=archived, retention_h=72, now=now)
    assert decide(JobState.DONE) == "job done" and decide(JobState.CANCELLED, kept=True) == "job cancelled"
    assert decide(JobState.IMPLEMENTING, archived=True) == "job archived"
    assert decide(JobState.PUSHED) == "job pushed" and decide(JobState.PUSHED, kept=True) == ""
    assert decide(JobState.FAILED, idle_h=73) == "idle retention" and decide(JobState.FAILED, idle_h=10) == ""
    assert decide(JobState.NEEDS_INPUT, idle_h=500, kept=True) == ""  # kept: exempt from idle retention
    assert decide(JobState.IMPLEMENTING, idle_h=500) == ""


def test_sweeper_selects_by_label_and_only_this_home(home, monkeypatch):
    service, row, repo = _service(home)
    seen = []

    def handler(args):
        seen.append(args)
        if args[0] == "ps" and "--format" in args:
            return f"c1 {row.id}\nc2 deadbeefdeadbeef\n"
        return ""
    cleaned = []
    monkeypatch.setattr(envs, "cleanup_job", lambda job_id, **kw: cleaned.append((job_id, kw.get("reason"))))
    result = envs.sweep(service, docker=FakeDocker(handler), settings={})
    assert ("deadbeefdeadbeef", "job deleted") in cleaned and all(j != row.id for j, _ in cleaned)
    assert result["cleaned"] == ["deadbeefdeadbeef"]
    assert all(f"label=io.mucli.home={envs.home_id()}" in a for a in seen if a[0] in ("ps", "volume", "network") and "ls" in a or a[0] == "ps")


# ------------------------------------------------------------------ proxy policy (§7)


def proxy_ctx(tmp_path, gpus=""):
    from mu.jobs.sandbox.docker_proxy import ProxyContext
    worktree, cache = tmp_path / "wt", tmp_path / "cache"
    (worktree / "sub").mkdir(parents=True, exist_ok=True)
    cache.mkdir(exist_ok=True)
    if not (worktree / "escape").is_symlink():
        (worktree / "escape").symlink_to("/etc")
    return ProxyContext(job_id="job1", worktree=str(worktree), cache_dir=str(cache), gpus=gpus,
                        egress=("registry-1.docker.io", "git.resqvision-util.com.au"),
                        owned=lambda ref: ref == "mine")


def test_proxy_bind_policy_judged_by_real_path(tmp_path):
    from mu.jobs.sandbox.docker_proxy import decide
    ctx = proxy_ctx(tmp_path)
    create = lambda host: decide("POST", "/v1.45/containers/create", {}, {"Image": "x", "HostConfig": host}, ctx)
    allowed = create({"Binds": ["/workspace/sub:/src", "/cache:/cache:ro", "named-vol:/data"]})
    assert allowed.allow and allowed.body["Labels"]["io.mucli.job"] == "job1"
    assert not create({"Binds": ["/etc:/etc"]}).allow
    assert not create({"Binds": ["/workspace/escape:/x"]}).allow  # symlink escaping the worktree
    assert not create({"Binds": ["/workspace/../etc:/x"]}).allow
    assert not create({"Mounts": [{"Type": "bind", "Source": "/var/run/docker.sock", "Target": "/s"}]}).allow
    assert create({"Mounts": [{"Type": "tmpfs", "Target": "/t"}, {"Type": "volume", "Source": "v", "Target": "/v"}]}).allow


def test_proxy_denies_devices_namespaces_and_foreign_containers(tmp_path):
    from mu.jobs.sandbox.docker_proxy import decide
    ctx = proxy_ctx(tmp_path)
    create = lambda host: decide("POST", "/containers/create", {}, {"HostConfig": host}, ctx)
    assert not create({"Devices": [{"PathOnHost": "/dev/kvm"}]}).allow
    for key in ("NetworkMode", "PidMode", "IpcMode", "UTSMode", "UsernsMode"):
        assert "host is not allowed" in create({key: "host"}).reason
    assert create({"Privileged": True, "NetworkMode": "bridge"}).allow  # confined to the Sysbox engine
    assert not create({"VolumesFrom": ["theirs"]}).allow and create({"VolumesFrom": ["mine:ro"]}).allow
    assert not decide("POST", "/containers/theirs/start", {}, None, ctx).allow
    assert decide("POST", "/containers/mine/start", {}, None, ctx).allow
    assert decide("GET", "/containers/json", {}, None, ctx).allow
    assert not decide("POST", "/plugins/pull", {}, None, ctx).allow
    assert decide("POST", "/networks/create", {}, {"Name": "n"}, ctx).body["Labels"]["io.mucli.job"] == "job1"
    assert not decide("POST", "/networks/create", {}, {"Driver": "host"}, ctx).allow
    assert not decide("POST", "/volumes/create", {}, {"DriverOpts": {"o": "bind", "device": "/etc"}}, ctx).allow
    build = decide("POST", "/build", {"t": ["x"]}, None, ctx)
    assert json.loads(build.query["labels"][0]) == {"io.mucli.job": "job1"}
    assert not decide("POST", "/build", {"networkmode": ["host"]}, None, ctx).allow


def test_proxy_pulls_only_allowlisted_registries(tmp_path):
    from mu.jobs.sandbox.docker_proxy import decide, registry_of
    ctx = proxy_ctx(tmp_path)
    assert registry_of("postgres") == "docker.io" and registry_of("git.resqvision-util.com.au/x/y") == "git.resqvision-util.com.au"
    assert decide("POST", "/images/create", {"fromImage": ["postgres"], "tag": ["16"]}, None, ctx).pull == "postgres:16"
    assert decide("POST", "/images/create", {"fromImage": ["git.resqvision-util.com.au/rsq/app"]}, None, ctx).allow
    denied = decide("POST", "/images/create", {"fromImage": ["ghcr.io/x/y"]}, None, ctx)
    assert not denied.allow and "egress allowlist" in denied.reason
    assert not decide("POST", "/images/create", {"fromSrc": ["http://evil/x.tar"]}, None, ctx).allow


def test_proxy_device_requests_within_the_binding_gpus(tmp_path):
    from mu.jobs.sandbox.docker_proxy import decide
    gpu = lambda request, gpus: decide("POST", "/containers/create", {},
                                       {"HostConfig": {"DeviceRequests": [request]}}, proxy_ctx(tmp_path / gpus, gpus))
    req = lambda **kw: {"Driver": "nvidia", "Capabilities": [["gpu"]], **kw}
    assert not gpu(req(Count=-1), "").allow
    assert gpu(req(Count=-1), "all").allow
    assert gpu(req(DeviceIDs=["0"]), "device=0,1").allow
    assert not gpu(req(DeviceIDs=["2"]), "device=0,1").allow
    assert not gpu(req(Count=-1), "device=0").allow
    assert not gpu({"Driver": "other", "Capabilities": [["tpu"]]}, "all").allow


# ------------------------------------------------------------------ commands, actions and VM shapes


def test_environment_commands_and_actions(home, monkeypatch):
    from mu.jobs.core.actions import action_map
    from mu.jobs.core.commands import CommandError, CommandRejected, dispatch
    from mu.jobs.core.vm import facts_for, job_vm
    service, row, repo = _service(home)
    service.release(row.id, "worker", reason="test")
    actions = action_map(facts_for(service, service.get(row.id)))
    assert actions["refreshEnvironments"].enabled is False and "shared runtime" in actions["refreshEnvironments"].reason
    assert actions["refreshEnvironments"].shortcut == "E" and actions["keepEnvironments"].shortcut == "K"
    assert actions["refreshEnvironments"].confirm.startswith("Re-snapshot the build environments")
    assert actions["setEnvironments"].dialog == "environments" and actions["setEnvironments"].hidden

    result = dispatch(service, {"type": "setEnvironments", "job_id": row.id,
                                "args": {"environments": ["rust-build:resq-backend", "image:node:22:paths=resq-frontend"]}})
    assert [b["name"] for b in result.data["environments"]["bindings"]] == ["rust-build", "node"]
    assert service.get(row.id).execution["environments"][0]["paths"] == ["resq-backend"]
    with pytest.raises(CommandError):
        dispatch(service, {"type": "setEnvironments", "job_id": row.id, "args": {"environments": ["image:bad image"]}})

    kept = dispatch(service, {"type": "keepEnvironments", "job_id": row.id})
    assert kept.data == {"kept": True} and service.get(row.id).metadata["environments_kept"] is True
    assert action_map(facts_for(service, service.get(row.id)))["keepEnvironments"].label == "Unkeep environments"
    assert dispatch(service, {"type": "keepEnvironments", "job_id": row.id}).data == {"kept": False}

    monkeypatch.setattr(envs, "cleanup_job", lambda *a, **k: {})
    monkeypatch.setattr(envs, "prepare_in_background", lambda service, job_id: None)
    refreshed = dispatch(service, {"type": "refreshEnvironments", "job_id": row.id})
    assert refreshed.data == {"refresh_requested": True}
    assert service.get(row.id).metadata["environments_refresh_requested"] is True

    assert service.acquire(row.id, "w2", ttl_seconds=60)
    with pytest.raises(CommandRejected, match="stage is running"):
        dispatch(service, {"type": "refreshEnvironments", "job_id": row.id})

    vm = job_vm(service, row.id, parts=["state"])
    assert set(vm["build_environments"]) >= {"source", "kept", "bindings", "count", "bytes", "summary"}
    assert vm["environments"]["count"] == vm["build_environments"]["count"]


def test_environments_vm_preview_shape(home, monkeypatch):
    from mu.jobs.core import vm
    repo = home / "repo"
    repo.mkdir()
    write_toml(repo, '[[environments]]\nname = "rust-build"\npaths = ["a"]\n\n[[environments]]\nname = "compose-it"\ndocker = true\n')
    monkeypatch.setattr(envs, "list_sources", lambda include_images=True: {"environments": [], "templates": [], "images": [],
                                                                           "docker_in_docker": {"available": False}, "gpu": {}})
    data = vm.environments_vm(repository=str(repo))
    assert data["resolved"]["source"] == "repo"
    assert data["resolved"]["summary"] == "rust-build → a · compose-it ⬢ docker"


def test_missing_tool_message_names_the_binding():
    from mu.jobs.verify_worker import missing_tool_message
    text = missing_tool_message({"missing_tools": ["cargo"], "missing_tool_environments": {"cargo": "rust-build"}})
    assert "Checks need `cargo`, which build environment `rust-build` doesn't include" in text
    docker = missing_tool_message({"missing_tools": ["docker"], "missing_tool_environments": {"docker": "compose-it"},
                                   "missing_docker_suites": [{"suite": "it", "environment": "compose-it"}]})
    assert docker == "Suite `it` needs Docker; enable Docker for binding `compose-it`."
    assert "Settings → Sandbox" in missing_tool_message({"missing_tools": ["cargo"]})


def test_checks_record_the_binding_that_ran_each_suite(home):
    from mu.jobs.checks import ChecksStage
    service, row, repo = _service(home)
    meta = dict(row.metadata or {})
    meta["build_environments"] = [{"name": "rust-build", "paths": ["svc"], "status": "ready"},
                                  {"name": "node-build", "paths": ["web"], "status": "ready"}]
    service.store.update_runtime_fields(row.id, metadata_json=meta,
                                        validation_json=["cargo test --manifest-path=svc/Cargo.toml", "npm --prefix web test"])
    seen = {}

    def run_suite(job, workspace, suite):
        return {"passed": True, "return_code": 0, "stdout": "", "stderr": ""}
    run = ChecksStage(service, run_suite=run_suite, settings={}).verify(service.get(row.id))
    by_suite = {s["suite"]: s["environment"] for s in run.summary["suites"]}
    assert by_suite == {"check1": "rust-build", "check2": "node-build"}


def test_policy_proxy_forwards_rewrites_and_denies_over_real_sockets(tmp_path):
    """HTTP plumbing: a real Unix-socket client → proxy → fake TCP engine."""
    import asyncio
    import threading
    import httpx
    from mu.jobs.sandbox.docker_proxy import DockerPolicyProxy, ProxyContext

    seen, events = [], []
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    port = []

    async def engine(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode().split("\r\n")
        length = next((int(l.split(":")[1]) for l in lines if l.lower().startswith("content-length")), 0)
        body = await reader.readexactly(length) if length else b""
        seen.append((lines[0], body))
        if "/containers/theirs/json" in lines[0]:
            payload = json.dumps({"Id": "theirs", "Config": {"Labels": {"io.mucli.job": "other"}}}).encode()
        else:
            payload = b'{"Id": "new"}'
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                     % (len(payload), payload))
        await writer.drain()
        writer.close()

    def serve():
        asyncio.set_event_loop(loop)
        server = loop.run_until_complete(asyncio.start_server(engine, "127.0.0.1", 0))
        port.append(server.sockets[0].getsockname()[1])
        ready.set()
        loop.run_forever()
    threading.Thread(target=serve, daemon=True).start()
    ready.wait(5)
    (tmp_path / "wt").mkdir()
    ctx = ProxyContext(job_id="job1", worktree=str(tmp_path / "wt"))
    proxy = DockerPolicyProxy(tmp_path / "docker.sock", ("127.0.0.1", port[0]), ctx, on_event=events.append).start()
    try:
        client = httpx.Client(transport=httpx.HTTPTransport(uds=str(tmp_path / "docker.sock")), timeout=10)
        ok = client.post("http://docker/v1.45/containers/create", json={"Image": "alpine", "HostConfig": {"Binds": ["/workspace:/w"]}})
        assert ok.status_code == 200 and ok.json() == {"Id": "new"}
        line, body = seen[-1]
        assert line.startswith("POST /v1.45/containers/create") and json.loads(body)["Labels"] == {"io.mucli.job": "job1"}
        denied = client.post("http://docker/containers/create", json={"HostConfig": {"Binds": ["/etc:/etc"]}})
        assert denied.status_code == 403 and "outside /workspace" in denied.json()["message"]
        foreign = client.post("http://docker/containers/theirs/stop")
        assert foreign.status_code == 403 and "not created by this job" in foreign.json()["message"]
        assert client.get("http://docker/_ping").status_code == 200
        assert [e["allowed"] for e in events if e["action"] in ("create", "stop")] == [True, False, False]
    finally:
        proxy.close()
        loop.call_soon_threadsafe(loop.stop)


def test_policy_proxy_keeps_half_closed_attach_streams_alive_across_gc(tmp_path):
    """Regression: a hijacked attach whose client half-closed must survive a GC pass (asyncio holds
    neither connection tasks nor stream readers strongly) and still deliver the container output."""
    import asyncio
    import gc
    import socket
    import threading
    from mu.jobs.sandbox.docker_proxy import DockerPolicyProxy, ProxyContext

    loop = asyncio.new_event_loop()
    ready, port, release = threading.Event(), [], threading.Event()

    async def engine(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        if b"/json" in head.split(b"\r\n")[0]:
            body = b'{"Id": "c1", "Config": {"Labels": {"io.mucli.job": "job1"}}}'
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(body), body))
        else:
            writer.write(b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n")
            await writer.drain()
            while not release.is_set():
                await asyncio.sleep(0.02)
            writer.write(b"container-output")
        await writer.drain()
        writer.close()

    def serve():
        asyncio.set_event_loop(loop)
        server = loop.run_until_complete(asyncio.start_server(engine, "127.0.0.1", 0))
        port.append(server.sockets[0].getsockname()[1])
        ready.set()
        loop.run_forever()
    threading.Thread(target=serve, daemon=True).start()
    ready.wait(5)
    (tmp_path / "wt").mkdir()
    proxy = DockerPolicyProxy(tmp_path / "d.sock", ("127.0.0.1", port[0]), ProxyContext(job_id="job1", worktree=str(tmp_path / "wt"))).start()
    try:
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(5)
        client.connect(str(tmp_path / "d.sock"))
        client.sendall(b"POST /containers/c1/attach?stream=1&stdout=1 HTTP/1.1\r\nHost: docker\r\nContent-Length: 0\r\n"
                       b"Connection: Upgrade\r\nUpgrade: tcp\r\n\r\n")
        received = b""
        while b"\r\n\r\n" not in received:
            received += client.recv(4096)
        assert received.startswith(b"HTTP/1.1 101")
        client.shutdown(socket.SHUT_WR)  # the CLI half-closes at once when there is no stdin
        import time
        time.sleep(0.1)
        gc.collect()
        release.set()
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            received += chunk
        assert received.endswith(b"container-output")
    finally:
        proxy.close()
        loop.call_soon_threadsafe(loop.stop)
