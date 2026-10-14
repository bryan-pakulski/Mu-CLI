"""Host-owned stage policy; never accept Docker options from an agent."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

IMAGE = "mucli-job-runtime:latest"
STAGES = ("plan", "implement", "review", "checks")

# Committed dotenv templates (`.env.example`, `.env.inference.example`,
# `.env.sample`, ...) hold placeholders, not secrets. Real dotenv files
# (`.env`, `.env.local`, `.env.production`) still fail closed.
ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults")


def is_env_template(basename: str) -> bool:
    name = basename.lower()
    return name.startswith(".env.") and name.endswith(ENV_TEMPLATE_SUFFIXES)


@dataclass(frozen=True)
class SandboxPolicy:
    image: str = IMAGE
    cpu: float = 4
    memory: str = "8g"
    timeout: int = 900
    egress_extra: tuple[str, ...] = ()
    max_file_bytes: int = 20 * 1024 * 1024
    # Per-stage model budget. Tokens count only NEW tokens (input that was not a
    # cache read, plus output) unless count_cache_reads is set: every turn re-sends
    # the whole context, so counting cache reads exhausts a stage after ~20 turns.
    max_tokens: int = 50_000_000
    max_cost_usd: float = 50.0
    count_cache_reads: bool = False
    # Per-repository toolchain (Settings → Toolchains): interpreter inside the derived
    # image and a host-owned per-job cache directory mounted at /cache.
    python: str = "python3"
    cache_dir: str = ""
    # Build environment (job_build_environments.md): owning job (labels every container),
    # binding name, Docker's --gpus request and the host path of the per-stage Docker
    # policy-proxy socket (mounted at /var/run/docker.sock for docker bindings).
    job_id: str = ""
    binding: str = ""
    gpus: str = ""
    docker_socket: str = ""
    # Process cap per stage container (`sandbox.pids_limit`): builds (cargo, gradle, npm,
    # parallel test runners) easily need far more than a few hundred.
    pids_limit: int = 4096
    # Tracked files the secret-path pre-check may mount anyway, from the base commit's
    # `.mucli/jobs.toml` `[sandbox] allow_paths = [glob, ...]` (never from the worktree).
    allow_paths: tuple[str, ...] = ()

    @classmethod
    def for_stage(cls, stage: str, settings: dict | None = None):
        if stage not in STAGES:
            raise ValueError(f"unknown job stage: {stage}")
        if settings is None:
            from ..job_settings import load_settings
            settings = load_settings()
        raw = settings.get("sandbox") or {}
        image = str(raw.get("image") or IMAGE)
        if not re.fullmatch(r"[\w./:@-]+", image) or image.startswith("-"):
            raise ValueError("invalid sandbox image")
        memory = str(raw.get("memory") or "8g")
        if not re.fullmatch(r"[1-9][0-9]*[kmgKMG]?", memory):
            raise ValueError("invalid sandbox memory limit")
        extra = raw.get("egress_extra") or []
        if not isinstance(extra, list) or any(not re.fullmatch(r"(?:\*\.)?[a-zA-Z0-9.-]+", str(x)) for x in extra):
            raise ValueError("sandbox.egress_extra must contain domain names")
        return cls(
            image=image, cpu=max(.1, min(64, float(raw.get("cpu", 4)))), memory=memory,
            timeout=max(1, min(86400, int((raw.get("stage_timeout_s") or {}).get(
                stage, 3600)))),
            egress_extra=tuple(extra) if stage in ("implement", "checks") else (),
            max_file_bytes=max(1, int(raw.get("max_file_bytes", 20 * 1024 * 1024))),
            max_tokens=max(1, int(raw.get("max_tokens", 50_000_000))),
            max_cost_usd=max(0, float(raw.get("max_cost_usd", 50))),
            count_cache_reads=bool(raw.get("count_cache_reads", False)),
            pids_limit=max(64, min(1_000_000, int(raw.get("pids_limit") or 4096))),
        )


def with_build_environment(policy: "SandboxPolicy", job, state, *, stage: str = "", settings=None) -> "SandboxPolicy":
    """Apply one prepared build-environment binding: its job image, venv Python, the shared
    dependency /cache, inherited egress (model hosts dropped) and GPUs. ``state`` None = shared
    runtime. The base commit's ``[sandbox] allow_paths`` and ``egress`` are merged in (egress only
    for implement and checks, and only ever added to the user's Settings allowlist)."""
    from dataclasses import replace
    allow = tuple(dict.fromkeys((*policy.allow_paths, *repository_allow_paths(job))))
    egress = tuple(dict.fromkeys((*policy.egress_extra, *repository_egress(job, stage))))
    if not state:
        return replace(policy, job_id=str(getattr(job, "id", "") or ""), allow_paths=allow, egress_extra=egress,
                       cache_dir=stage_cache_dir(job, None, stage, settings))
    from ..job_settings import rule
    from .environments import filter_model_hosts
    from .toolchain import VENV_PYTHON
    if stage in ("implement", "checks") and rule("environments.inherit_egress", settings):
        egress = tuple(dict.fromkeys((*egress, *filter_model_hosts(state.get("egress_allow") or []))))
    gpus = str(state.get("gpus") or "") if rule("sandbox.gpus_enabled", settings) else ""
    return replace(policy, image=str(state["job_image"]), python=str(state.get("python") or VENV_PYTHON),
                   cache_dir=stage_cache_dir(job, state, stage, settings), egress_extra=egress,
                   job_id=str(job.id), binding=str(state["name"]), gpus=gpus, allow_paths=allow)


def cache_toolchain_key(state) -> str:
    """Shared-cache toolchain key: the binding name plus its source (a renamed or re-pointed
    binding never reuses another toolchain's cache)."""
    if not state:
        return "runtime"
    import hashlib
    source = f"{state.get('source') or ''}:{state.get('ref') or ''}"
    return f"{state.get('name') or 'env'}-{hashlib.sha1(source.encode()).hexdigest()[:8]}"


def stage_cache_dir(job, state, stage: str, settings=None) -> str:
    """Host directory mounted at /cache: the shared per-(repository, toolchain, lockfile) cache
    for implement and checks; none for plan and review. Falls back to the per-job binding cache
    when the job has no repository identity; '' = no /cache."""
    if stage in ("plan", "review"):
        return ""
    try:
        from .cache import shared_cache_dir
        path = shared_cache_dir(job, cache_toolchain_key(state), tuple(state.get("paths") or ()) if state else (),
                                settings=settings)
    except (OSError, ValueError):
        path = ""
    if path or not state:
        return path
    from .toolchain import binding_cache_dir
    return str(binding_cache_dir(job, str(state["name"])))


def gpus_arg(request: str) -> str:
    """Docker parses ``--gpus`` as CSV: a device list needs its own quotes."""
    request = str(request or "")
    return f'"{request}"' if request.startswith("device=") and "," in request else request


def container_args(name: str, stage: str, workspace: str, inputs: str, output: str,
                   relay: str, policy: SandboxPolicy, token: str, *, role: str = "job-stage",
                   harness_mounts: tuple = (), module: str = "mu.jobs.sandbox.worker",
                   on_warning=None) -> list[str]:
    """Network-none + a scoped Unix socket avoids exposing any host TCP service.

    Only the disposable output directory is writable apart from implement's
    worktree. The gitdir crosses the boundary read-only (see ``git_vcs_mounts``);
    credentials, remotes and hooks never do.
    """
    if stage not in STAGES:
        raise ValueError("unknown stage")
    from .cli import bind_mount, is_podman, selinux_enforcing, user_args
    podman, selinux = is_podman(), selinux_enforcing()
    args = ["create", "--name", name, "--pull", "never", "--network", "none",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--read-only", "--pids-limit", str(int(policy.pids_limit)), "--cpus", str(policy.cpu),
            "--memory", policy.memory, *user_args(podman=podman),
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
            "--workdir", "/workspace", "--label", f"io.mucli.role={role}"]
    if policy.job_id:
        from .environments import job_labels
        for key, value in job_labels(policy.job_id).items():
            args += ["--label", f"{key}={value}"]
    if policy.binding:
        args += ["--label", f"io.mucli.binding={policy.binding}"]
    if policy.gpus:
        args += ["--gpus", gpus_arg(policy.gpus), "--label", f"io.mucli.gpus={policy.gpus}"]
    mounts = [(workspace, "/workspace", stage not in ("implement", "checks")),
              (inputs, "/artifacts", True), (output, "/output", False), (relay, "/relay", True)]
    if policy.cache_dir:
        mounts.append((policy.cache_dir, "/cache", False))
    if policy.docker_socket:
        mounts.append((policy.docker_socket, "/var/run/docker.sock", False))
    # Claude Code / Codex stages: the host CLI (read-only), the copied credential (read-only)
    # and the CLI's session store. Only these fixed destinations are accepted.
    for src, dst, readonly in harness_mounts:
        if dst not in ("/opt/harness/claude", "/opt/harness/codex", "/opt/harness/codex/bin/codex",
                       "/harness/creds", "/harness/state"):
            raise ValueError(f"unsupported harness mount {dst}")
        if dst != "/harness/state" and not readonly:
            raise ValueError(f"harness mount {dst} must be read-only")
        mounts.append((src, dst, readonly))
    for src, dst, readonly in mounts:
        args += bind_mount(str(Path(src).resolve(strict=True)), dst, readonly, podman=podman, selinux=selinux)
    # The worktree's .git pointer must not be replaced by the agent. VCS-aware tools
    # (go -buildvcs, setuptools-scm, husky, git describe) get the job worktree's private
    # gitdir plus a minimal HEAD-only object store READ-ONLY and a sanitized config (no
    # remotes, credentials, hooks, other refs or history); the host still owns every commit.
    pointer = Path(workspace) / ".git"
    vcs_mounts: list[tuple[str, str]] = []
    if pointer.exists():
        if not pointer.is_file() or pointer.is_symlink():
            raise ValueError("job sandbox requires a managed git worktree")
        vcs_mounts = git_vcs_mounts(workspace, on_warning=on_warning)
        if not vcs_mounts:
            vcs_mounts = [(str(pointer.resolve()), "/workspace/.git")]
    for src, dst in vcs_mounts:
        args += bind_mount(str(Path(src).resolve(strict=True)), dst, True, podman=podman, selinux=selinux)
    workspace_precheck(workspace, allow_paths=policy.allow_paths)
    for key, value in {"HOME": "/tmp", "MUCLI_HOME": "/tmp/mucli",
                       "MUCLI_MODEL_RELAY": "/relay/model.sock", "MUCLI_STAGE_TOKEN": token,
                       "MUCLI_JOB_STAGE": stage, "PYTHONDONTWRITEBYTECODE": "1",
                       "GOFLAGS": "-buildvcs=auto"}.items():
        args += ["-e", f"{key}={value}"]
    if len(vcs_mounts) > 1:
        # Read-only gitdir: never take optional locks (git status would try to refresh the
        # index); trust the mounted repository whatever uid owns it.
        args += ["-e", "GIT_OPTIONAL_LOCKS=0", "-e", "GIT_CONFIG_COUNT=1",
                 "-e", "GIT_CONFIG_KEY_0=safe.directory", "-e", "GIT_CONFIG_VALUE_0=*"]
    if policy.cache_dir:
        from .toolchain import CACHE_ENV
        for key, value in CACHE_ENV.items():
            if key == "CARGO_TARGET_DIR" and policy.job_id:
                from .cache import _slug
                value = f"{value}/{_slug(policy.job_id[:12])}"
            args += ["-e", f"{key}={value}"]
    if policy.docker_socket:
        args += ["-e", "DOCKER_HOST=unix:///var/run/docker.sock"]
    if module not in ("mu.jobs.sandbox.worker", "mu.jobs.sandbox.harness_worker"):
        raise ValueError(f"unsupported stage worker {module}")
    return args + ["--entrypoint", policy.python, policy.image, "-m", module]


# In-container layout of the read-only git metadata (see git_vcs_mounts).
GIT_CONTAINER_COMMON = "/mucli-git/common"
GIT_CONTAINER_GITDIR = GIT_CONTAINER_COMMON + "/worktrees/job"
# Entries of the per-job minimal object store (git_object_mirror) mounted read-only. Never the
# repository's own common dir: its objects/refs hold every branch, other jobs, refs/stash
# (which may include untracked files such as .env) and deleted-but-committed secrets.
_GIT_MIRROR_ENTRIES = ("objects", "refs", "packed-refs", "shallow", "reftable")
# Ignore/attribute files copied from the common dir's info/ (so git status matches the host).
_GIT_INFO_FILES = ("exclude", "attributes", "sparse-checkout")
_MIRROR_KEEP = 3   # store generations kept on the host (a running broker may still mount one)
# Repository config keys that are safe and needed to read the repository.
_GIT_SAFE_CONFIG = {
    "core.repositoryformatversion", "core.filemode", "core.symlinks", "core.ignorecase",
    "core.precomposeunicode", "core.autocrlf", "core.eol", "core.safecrlf", "core.quotepath",
    "extensions.objectformat", "extensions.refstorage", "extensions.worktreeconfig",
}


def _git_state_dir(private_gitdir: str) -> Path:
    import hashlib
    from ..core.artifacts import jobs_root
    digest = hashlib.sha256(private_gitdir.encode("utf-8")).hexdigest()[:16]
    path = Path(jobs_root()) / ".sandbox-git" / digest
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_atomic(path: Path, text: str) -> str:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


def _safe_config_values(config_file: str) -> dict[str, str]:
    import subprocess
    values: dict[str, str] = {}
    try:
        result = subprocess.run(["git", "config", "--file", config_file, "--list", "-z"],
                                capture_output=True, timeout=30)
        entries = result.stdout.decode("utf-8", "replace").split("\0") if result.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        entries = []
    for entry in entries:
        key, _, value = entry.partition("\n")
        key = key.strip().lower()
        if key in _GIT_SAFE_CONFIG and re.fullmatch(r"[\w.+-]{1,64}", value or ""):
            values[key] = value
    return values


def sanitized_git_config(common_dir: str, format_dir: str = "") -> str:
    """Repository config reduced to format/core keys: no remotes (tokens), includes,
    credential helpers, aliases, filters or hooks paths; hooks are disabled outright.
    ``format_dir``: the object store actually mounted — its repository format and
    extensions (object format, ref storage) replace the source repository's."""
    values = _safe_config_values(os.path.join(common_dir, "config"))
    if format_dir:
        values = {k: v for k, v in values.items()
                  if k not in ("core.repositoryformatversion", "extensions.objectformat", "extensions.refstorage")}
        values.update({k: v for k, v in _safe_config_values(os.path.join(format_dir, "config")).items()
                       if k in ("core.repositoryformatversion", "extensions.objectformat", "extensions.refstorage")})
    values.setdefault("core.repositoryformatversion", "0")
    if values["core.repositoryformatversion"] not in ("0", "1"):
        values["core.repositoryformatversion"] = "0"
    sections: dict[str, list[str]] = {"core": ["\tbare = false", "\thooksPath = /dev/null"]}
    for key in sorted(values):
        section, name = key.split(".", 1)
        sections.setdefault(section, []).append(f"\t{name} = {values[key]}")
    return "".join(f"[{name}]\n" + "\n".join(lines) + "\n" for name, lines in sections.items())


def _host_git(*args: str, timeout: int = 300):
    import subprocess
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.version=2", *args],
                          capture_output=True, timeout=timeout, env=env)


def git_object_mirror(gitdir: Path, common: Path, state: Path) -> Path:
    """A minimal bare object store holding only the job worktree's HEAD commit (shallow,
    depth 1): its one branch ref and nothing else — no other branches or jobs, no tags, no
    ``refs/stash``, no history (so no deleted-but-committed secret). Rebuilt whenever HEAD
    moves (before each stage launch), reused while it does not. Raises on failure."""
    import shutil
    import subprocess
    head = _host_git(f"--git-dir={gitdir}", "rev-parse", "--verify", "-q", "HEAD^{commit}", timeout=60)
    sha = head.stdout.decode("ascii", "replace").strip()
    if head.returncode or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        raise RuntimeError("the job worktree has no HEAD commit")
    symbolic = _host_git(f"--git-dir={gitdir}", "symbolic-ref", "-q", "HEAD", timeout=60)
    ref = symbolic.stdout.decode("utf-8", "replace").strip() if symbolic.returncode == 0 else ""
    if not re.fullmatch(r"refs/heads/[\w./+@-]{1,200}", ref) or ".." in ref:
        ref = "refs/heads/mucli-job"   # detached HEAD: the commit still needs a ref to live under
    fmt = _host_git(f"--git-dir={common}", "rev-parse", "--show-object-format", timeout=60)
    object_format = fmt.stdout.decode("ascii", "replace").strip() if fmt.returncode == 0 else "sha1"
    if object_format not in ("sha1", "sha256"):
        object_format = "sha1"
    digest = __import__("hashlib").sha256(f"{sha}\0{ref}".encode()).hexdigest()[:20]
    store = state / f"store-{digest}"
    if not (store / ".mucli-complete").is_file():
        tmp = state / f".store-{digest}.{os.getpid()}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            steps = (("init", "-q", "--bare", f"--object-format={object_format}", str(tmp)),
                     (f"--git-dir={tmp}", "fetch", "-q", "--depth=1", "--no-tags", "--no-write-fetch-head",
                      "--no-recurse-submodules", "--upload-pack",
                      "git -c uploadpack.allowAnySHA1InWant=true upload-pack", str(common), f"+{sha}:{ref}"))
            for step in steps:
                result = _host_git(*step)
                if result.returncode:
                    raise RuntimeError(result.stderr.decode("utf-8", "replace").strip()[-300:] or "git failed")
            refs = _host_git(f"--git-dir={tmp}", "for-each-ref", "--format=%(refname)", timeout=60)
            if refs.stdout.decode("utf-8", "replace").split() != [ref]:
                raise RuntimeError("object mirror holds unexpected refs")
            if (tmp / "objects" / "info" / "alternates").exists():
                raise RuntimeError("object mirror must not borrow objects")
            shutil.rmtree(tmp / "hooks", ignore_errors=True)
            (tmp / "info").mkdir(exist_ok=True)
            for name in _GIT_INFO_FILES:
                source = common / "info" / name
                if source.is_file() and not source.is_symlink():
                    shutil.copyfile(source, tmp / "info" / name)
            (tmp / ".mucli-complete").write_text(sha + "\n")
            shutil.rmtree(store, ignore_errors=True)
            os.replace(tmp, store)
        except (OSError, subprocess.SubprocessError, RuntimeError):
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    generations = sorted((p for p in state.glob("store-*") if p.is_dir() and p != store),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    for old in generations[_MIRROR_KEEP - 1:]:
        shutil.rmtree(old, ignore_errors=True)
    os.utime(store)
    return store


def git_vcs_mounts(workspace: str, *, on_warning=None) -> list[tuple[str, str]]:
    """Read-only ``(host source, container destination)`` mounts that make the job
    worktree a working (read-only) git checkout inside the container.

    The worktree's private gitdir (index, HEAD) is mounted at ``/mucli-git/common/worktrees/job``
    and beside it a per-job minimal object store (:func:`git_object_mirror`: only the HEAD
    commit, shallow, one ref) in place of the repository's common dir, with a generated
    ``commondir`` (``../..``), a sanitized ``config`` and a rewritten ``/workspace/.git`` pointer
    so every path resolves in-container. ``git status``/``log -1``/``rev-parse``, go -buildvcs and
    setuptools-scm work; history and tags are absent (``git describe`` falls back to its
    no-tag form). Returns ``[]`` when the pointer does not reference a usable linked worktree
    or the store cannot be built (the caller then mounts the bare pointer file; ``on_warning``
    receives the reason)."""
    pointer = Path(workspace) / ".git"
    try:
        text = pointer.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return []
    if not text.startswith("gitdir:"):
        return []
    target = text[len("gitdir:"):].strip()
    gitdir = Path(target if os.path.isabs(target) else os.path.join(workspace, target))
    gitdir = Path(os.path.realpath(gitdir))
    commondir_file = gitdir / "commondir"
    if not gitdir.is_dir() or not (gitdir / "HEAD").is_file() or not commondir_file.is_file():
        return []
    raw_common = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
    common = Path(os.path.realpath(raw_common if os.path.isabs(raw_common) else gitdir / raw_common))
    if not (common / "objects").is_dir() or not ((common / "refs").is_dir() or (common / "reftable").is_dir()):
        return []
    # Never let a crafted pointer expose hooks or the common dir itself as the "gitdir".
    if gitdir == common or (gitdir / "hooks").exists():
        return []
    state = _git_state_dir(str(gitdir))
    try:
        store = git_object_mirror(gitdir, common, state)
    except Exception as exc:  # noqa: BLE001 - fall back to the pointer-only mount
        import logging
        message = f"git metadata unavailable in the sandbox (pointer only, no history): {exc}"
        logging.getLogger(__name__).warning(message)
        if on_warning is not None:
            try:
                on_warning(message)
            except Exception:  # noqa: BLE001
                pass
        return []
    mounts = [(str(gitdir), GIT_CONTAINER_GITDIR)]
    for name in (*_GIT_MIRROR_ENTRIES, "info"):
        source = store / name
        if source.exists() and not source.is_symlink():
            mounts.append((str(source), f"{GIT_CONTAINER_COMMON}/{name}"))
    mounts.append((_write_atomic(state / "config", sanitized_git_config(str(common), str(store))),
                   f"{GIT_CONTAINER_COMMON}/config"))
    mounts.append((_write_atomic(state / "commondir", "../..\n"), f"{GIT_CONTAINER_GITDIR}/commondir"))
    mounts.append((_write_atomic(state / "pointer", f"gitdir: {GIT_CONTAINER_GITDIR}\n"), "/workspace/.git"))
    return mounts


# Dependency/build trees: produced by package managers and builds, never hand-written
# secrets. Skipped by the pre-check unless they contain tracked files.
VENDOR_DIRS = frozenset({"node_modules", ".venv", "venv", "target", "build", "dist", ".gradle", "__pycache__"})


def _git_lines(workspace: str, *args: str) -> list[str] | None:
    import subprocess
    try:
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", workspace, *args],
                                capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    return [p for p in result.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def _git_view(workspace: str):
    """(tracked files, ignored untracked paths) from the host's git; None when ``workspace`` is
    not itself a git checkout (a parent repository's ignore rules never apply)."""
    if not (Path(workspace) / ".git").exists():
        return None
    top = _git_lines(workspace, "rev-parse", "--show-toplevel")
    if not top or os.path.realpath(top[0].strip()) != os.path.realpath(workspace):
        return None
    tracked = _git_lines(workspace, "ls-files", "-z", "--cached")
    ignored = _git_lines(workspace, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory")
    if tracked is None or ignored is None:
        return None
    return set(tracked), {p.rstrip("/") for p in ignored}


def repository_allow_paths(job) -> tuple[str, ...]:
    """``[sandbox] allow_paths`` from ``.mucli/jobs.toml`` at the job's base commit.

    Read from the base commit, not the worktree, so a stage cannot allow its own files."""
    worktree, base = str(getattr(job, "worktree", "") or ""), str(getattr(job, "base_sha", "") or "")
    if not worktree or not base or not os.path.isdir(worktree):
        return ()
    from .repo_config import repository_sandbox
    return tuple(repository_sandbox(job)["allow_paths"])


def repository_egress(job, stage: str) -> tuple[str, ...]:
    """``[sandbox] egress`` from the base commit: implement and checks only (plan and review
    reach nothing but the model relay), and only once the user approved it for the repository
    (unapproved, it is not applied; see ``repo_config.egress_approval``)."""
    if stage not in ("implement", "checks") or not str(getattr(job, "base_sha", "") or ""):
        return ()
    from .repo_config import approved_repository_egress
    return approved_repository_egress(job)


def _allowed(relative: str, allow_paths, *, is_dir: bool = False) -> bool:
    """``relative`` matches an allow glob (a directory also when a glob reaches inside it)."""
    from fnmatch import fnmatchcase
    for pattern in allow_paths:
        if any(fnmatchcase(relative, p) for p in (pattern, pattern.rstrip("/"), pattern.rstrip("/") + "/*")):
            return True
        if is_dir and pattern.startswith(relative.rstrip("/") + "/"):
            return True
    return False


def workspace_precheck(workspace: str, *, allow_paths=()) -> None:
    """Fail closed before a worktree is mounted: no escaping symlinks, no secret files.

    Skipped (never host secrets: git ignores them, or a package manager / build made them):
    gitignored untracked paths and vendor/build trees without tracked files. A tracked
    denied file may be mounted when the base commit's ``.mucli/jobs.toml`` lists it in
    ``[sandbox] allow_paths``."""
    from mu.security.secret_paths import is_denied_path
    root = Path(workspace)
    real_root = root.resolve()
    view = _git_view(workspace)
    tracked, ignored = view if view is not None else (set(), set())
    tracked_dirs = {str(Path(p).parent) for p in tracked}
    tracked_dirs = {d for p in tracked_dirs for d in [p, *map(str, Path(p).parents)]}

    def skip(relative: str, name: str, is_dir: bool) -> bool:
        if relative in ignored:
            return True
        return is_dir and name in VENDOR_DIRS and relative not in tracked_dirs
    for directory, dirs, files in os.walk(workspace, followlinks=False):
        base = Path(directory)
        rel_dir = base.relative_to(root)
        dirs[:] = [d for d in dirs if d != ".git" and not skip(str(rel_dir / d), d, True)]
        for filename in [*dirs, *files]:
            path = base / filename
            relative = str(rel_dir / filename)
            if relative in ignored:
                continue
            if path.is_symlink() and not path.resolve().is_relative_to(real_root):
                raise ValueError(f"workspace symlink escapes the sandbox: {relative}")
            if is_denied_path(str(path))[0] and not is_env_template(filename):
                is_dir = relative in tracked_dirs
                if (relative in tracked or is_dir) and _allowed(relative, allow_paths, is_dir=is_dir):
                    continue
                # Fail closed: an arbitrary shell could bypass a tool-level
                # denylist if secret files were mounted with the workspace.
                raise ValueError(f"secret path must be removed from the job worktree before sandboxing: {relative} "
                                 "(or, if it is a test fixture, list it under [sandbox] allow_paths in the "
                                 "repository's .mucli/jobs.toml)")


def validate_changes(workspace: str, paths: list[str], *, allowed_workflows=(),
                     max_file_bytes: int = 20 * 1024 * 1024) -> None:
    root = Path(workspace).resolve()
    for name in paths:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            raise ValueError(f"unsafe changed path: {name}")
        if name.startswith(".github/workflows/") and name not in allowed_workflows:
            raise ValueError(f"workflow change is not in the approved plan: {name}")
        path = root / relative
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"changed path escapes the worktree: {name}")
        if path.is_file() and path.stat().st_size > max_file_bytes:
            raise ValueError(f"changed file exceeds sandbox size limit: {name}")
