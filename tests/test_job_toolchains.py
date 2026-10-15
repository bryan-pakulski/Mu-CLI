"""Per-repository toolchain images (Settings → Toolchains)."""
from types import SimpleNamespace

import pytest

from mu.jobs.sandbox import toolchain as tc
from mu.jobs.sandbox.policy import SandboxPolicy, container_args, with_build_environment


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    return tmp_path


def job(repo, **meta):
    return SimpleNamespace(id="abcdef0123456789", repository=str(repo), metadata=meta, execution={})


def rows(*pairs):
    from mu.jobs.job_settings import save_settings
    save_settings({"sandbox": {"toolchains": [{"repository": str(r), "base_image": b} for r, b in pairs]}})


def test_repository_matching_prefers_the_longest_root(home):
    mono, service = home / "rsq-cloud", home / "rsq-cloud" / "stream-processor-rs"
    service.mkdir(parents=True)
    rows((mono, "ubuntu:24.04"), (service, "rust:1.89-bookworm"))
    assert tc.toolchain_for(job(mono))["base_image"] == "ubuntu:24.04"
    assert tc.toolchain_for(job(home / "elsewhere")) is None
    assert tc.toolchain_for(job("", repository_root=str(service)))["base_image"] == "rust:1.89-bookworm"
    assert tc.toolchain_for(job(home / "rsq-cloud-other")) is None  # sibling with a shared prefix


def test_derived_tag_is_stable_and_tracks_base_and_source(home, tmp_path):
    src = tmp_path / "src"
    (src / "mu" / "jobs").mkdir(parents=True)
    (src / "mu" / "jobs" / "x.py").write_text("a = 1\n")
    a = tc.derived_tag("rust:1.89-bookworm", src)
    assert a == tc.derived_tag("rust:1.89-bookworm", src) and a.startswith("mucli-job-toolchain:rust-1-89-bookworm-")
    assert a != tc.derived_tag("rust:1.90-bookworm", src)
    (src / "mu" / "jobs" / "x.py").write_text("a = 2\n")
    assert a != tc.derived_tag("rust:1.89-bookworm", src)


def test_dockerfile_keeps_the_base_toolchain_and_isolates_mucli_python():
    text = tc.dockerfile("rust:1.89-bookworm", {"RUSTUP_HOME": "/usr/local/rustup", "PATH": "/usr/local/cargo/bin:/usr/bin"})
    assert text.startswith("FROM rust:1.89-bookworm")
    assert "python3 -m venv /opt/mucli-venv" in text and 'ENV RUSTUP_HOME="/usr/local/rustup"' in text
    assert 'ENV PATH="/usr/local/cargo/bin:/usr/bin"' in text  # mucli's venv is NOT put on PATH
    assert "apt-get" in text and "apk add" in text and "chmod 1777 /cache" in text


def test_container_args_use_the_toolchain_python_and_per_job_cache(home, tmp_path):
    dirs = [tmp_path / n for n in ("ws", "in", "out", "relay", "cache")]
    for d in dirs:
        d.mkdir()
    policy = SandboxPolicy(image="mucli-job-toolchain:x", python=tc.VENV_PYTHON, cache_dir=str(dirs[4]))
    args = container_args("n", "checks", *map(str, dirs[:4]), policy, "")
    rendered = " ".join(args)
    assert f"src={dirs[4].resolve()},dst=/cache" in rendered and "CARGO_TARGET_DIR=/cache/target" in rendered
    assert args[-5:] == ["--entrypoint", tc.VENV_PYTHON, "mucli-job-toolchain:x", "-m", "mu.jobs.sandbox.worker"]
    plain = " ".join(container_args("n", "checks", *map(str, dirs[:4]), SandboxPolicy(), ""))
    assert "dst=/cache" not in plain and "--entrypoint python3" in plain


def test_toolchain_rows_resolve_to_per_job_build_environment_bindings(home):
    """Settings → Toolchains rows are the 4th binding source; bare values are images (planos-style)."""
    from mu.jobs.sandbox.environments import resolve_bindings
    repo = home / "repo"
    repo.mkdir()
    from mu.jobs.job_settings import save_settings
    save_settings({"sandbox": {"toolchains": [
        {"repository": str(repo), "base_image": "rust:1.89-bookworm", "paths": "backend, edge"},
        {"repository": str(repo), "base_image": "environment:node-build", "paths": "frontend", "docker": "yes"}]}})
    bindings, source = resolve_bindings(job(repo))
    assert source == "settings"
    assert [(b.name, b.source, b.ref, b.paths, b.docker) for b in bindings] == [
        ("rust", "image", "rust:1.89-bookworm", ("backend", "edge"), False),
        ("node-build", "environment", "node-build", ("frontend",), True)]
    assert resolve_bindings(job(home / "other")) == ([], "default")


def test_with_build_environment_swaps_image_python_cache_and_gpus(home):
    # Extra egress cleared so only the environment's own allowlist is under test.
    policy = SandboxPolicy.for_stage("checks", {"sandbox": {"egress_extra": []}})
    state = {"name": "rust-build", "job_image": "mucli/job-abcdef012345-rust-build-runtime:1", "gpus": "device=0",
             "egress_allow": ["crates.io", "api.openai.com", "*.anthropic.com"], "status": "ready"}
    swapped = with_build_environment(policy, job(home), state, stage="checks")
    assert swapped.image == state["job_image"] and swapped.python == tc.VENV_PYTHON
    # W9: the shared dependency cache (repository, toolchain, lockfile hash), not a per-job directory.
    assert "/cache/deps/" in swapped.cache_dir and "/rust-build-" in swapped.cache_dir and swapped.binding == "rust-build"
    assert swapped.egress_extra == ("crates.io",)  # model-provider hosts are never inherited
    assert swapped.gpus == "device=0" and swapped.job_id == "abcdef0123456789"
    plain = with_build_environment(policy, job(home), None, stage="checks")
    assert plain.image == policy.image and plain.job_id == "abcdef0123456789"


def test_toolchain_rows_are_a_validated_setting(home):
    from mu.jobs.job_settings import FIELD_BY_PATH, save_settings
    field = FIELD_BY_PATH["sandbox.toolchains"]
    assert field["kind"] == "rows" and [c["key"] for c in field["columns"]] == ["repository", "base_image", "paths", "docker", "gpus"]
    with pytest.raises(ValueError, match="Base image"):
        save_settings({"sandbox": {"toolchains": [{"repository": "/x", "base_image": "bad image; rm -rf /"}]}})
    with pytest.raises(ValueError, match="Repository path is required"):
        save_settings({"sandbox": {"toolchains": [{"base_image": "rust:1.89"}]}})
