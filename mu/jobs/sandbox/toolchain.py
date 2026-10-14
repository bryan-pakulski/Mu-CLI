"""Per-repository toolchain images (planos' per-repo ``image``).

Settings → Toolchains maps a repository to a *base* image that already has the project's
toolchain (often the project's own builder/CI image). mucli derives a sandbox image from it
by adding a private Python venv with mucli's code plus git/patch/ripgrep, and runs that
repository's implement and checks stages in it. The base's own PATH/toolchain environment
is preserved; mucli's Python lives in /opt/mucli-venv so it never shadows the project's.

Each job also gets a persistent, host-owned cache directory mounted at /cache: package
registries and build outputs (cargo, go, npm, pip, gradle, ccache) go there, so the
read-only rootfs and the small /tmp tmpfs are enough and the worktree stays clean.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

IMAGE_RE = re.compile(r"[\w./:@-]+")
TAG_PREFIX = "mucli-job-toolchain"
VENV_PYTHON = "/opt/mucli-venv/bin/python"

# Language caches redirected into the /cache mount (shared per repository + toolchain +
# lockfile hash, see cache.py). CARGO_TARGET_DIR is build output, not a download cache:
# container_args makes it per job (``/cache/target/<job12>``) so jobs never share artefacts.
CACHE_ENV = {
    "XDG_CACHE_HOME": "/cache/xdg", "CARGO_HOME": "/cache/cargo", "CARGO_TARGET_DIR": "/cache/target",
    "GOPATH": "/cache/go", "GOCACHE": "/cache/go-build", "GOMODCACHE": "/cache/go/pkg/mod",
    "npm_config_cache": "/cache/npm", "PIP_CACHE_DIR": "/cache/pip", "GRADLE_USER_HOME": "/cache/gradle",
    "CCACHE_DIR": "/cache/ccache", "MAVEN_OPTS": "-Dmaven.repo.local=/cache/m2",
    "UV_CACHE_DIR": "/cache/uv", "POETRY_CACHE_DIR": "/cache/poetry", "PDM_CACHE_DIR": "/cache/pdm",
    "HATCH_CACHE_DIR": "/cache/hatch", "YARN_CACHE_FOLDER": "/cache/yarn", "PNPM_STORE_DIR": "/cache/pnpm-store",
    "npm_config_store_dir": "/cache/pnpm-store", "BUN_INSTALL_CACHE_DIR": "/cache/bun", "DENO_DIR": "/cache/deno",
    "NUGET_PACKAGES": "/cache/nuget", "HEX_HOME": "/cache/hex", "MIX_HOME": "/cache/mix",
    "BUNDLE_USER_CACHE": "/cache/bundle", "COURSIER_CACHE": "/cache/coursier",
}
# Toolchain homes probed in the base image and re-exported so tools still find their
# installation when the stage runs as the host user with HOME=/tmp.
PROBED_HOMES = ("RUSTUP_HOME", "GOROOT", "JAVA_HOME", "ANDROID_HOME", "DOTNET_ROOT")


def toolchain_rows(settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    from ..job_settings import rule
    rows = rule("sandbox.toolchains", settings) if settings is not None else rule("sandbox.toolchains")
    return [dict(r) for r in (rows or []) if isinstance(r, dict) and r.get("repository") and r.get("base_image")]


def job_repository(job: Any) -> str:
    meta = dict(getattr(job, "metadata", None) or {})
    return str(meta.get("repository_root") or getattr(job, "repository", "") or "")


def toolchain_for(job: Any, settings: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
    """The toolchain row whose repository is the job's repository (or contains it); longest match wins."""
    repo = os.path.realpath(job_repository(job)) if job_repository(job) else ""
    if not repo:
        return None
    best = None
    for row in toolchain_rows(settings):
        root = os.path.realpath(os.path.expanduser(str(row["repository"]).strip()))
        if repo == root or repo.startswith(root.rstrip("/") + "/"):
            if best is None or len(root) > len(best[0]):
                best = (root, row)
    return best[1] if best else None


def _source_digest(source: Path) -> str:
    """Changes to mucli's own sandbox code must produce a new derived image."""
    digest = hashlib.sha1()
    for path in sorted((source / "mu" / "jobs").rglob("*.py")):
        digest.update(path.relative_to(source).as_posix().encode())
        digest.update(path.read_bytes())
    for name in ("requirements.txt", "mu/jobs/sandbox/toolchain.py"):
        if (source / name).is_file():
            digest.update((source / name).read_bytes())
    return digest.hexdigest()


def source_root() -> Path:
    return Path(__file__).resolve().parents[3]


def derived_tag(base_image: str, source: Optional[Path] = None) -> str:
    key = hashlib.sha1(f"{base_image}\n{_source_digest(source or source_root())}".encode()).hexdigest()[:16]
    slug = re.sub(r"[^a-z0-9]+", "-", base_image.lower().split("@")[0]).strip("-")[-40:] or "base"
    return f"{TAG_PREFIX}:{slug}-{key}"


# Container-mode environments bake these in; a job image must never look like container mode.
NEUTRALISED_ENV = ("MUCLI_CONTAINER_MODE", "MUCLI_WORKER_TOKEN", "MUCLI_WORKER_PORT", "MUCLI_WORKER_LOG_LEVEL",
                   "MUCLI_SUPERVISOR_URL", "MUCLI_CONTAINER_NAME", "MUCLI_WORKSPACES", "MUCLI_EGRESS_ALLOW",
                   "MUCLI_EGRESS_DENY", "MUCLI_PROXY_URL")


def env_value(value: str) -> str:
    """A value safe inside ``ENV K="..."`` (Docker expands ``$`` there)."""
    return str(value).replace("\\", "\\\\").replace("$", "\\$")


def dockerfile(base_image: str, probed_env: Dict[str, str], labels: Optional[Dict[str, str]] = None) -> str:
    env_lines = "".join(f'ENV {k}="{env_value(v)}"\n' for k, v in sorted(probed_env.items()))
    neutral = " ".join(f'{k}=""' for k in NEUTRALISED_ENV)
    label_line = " ".join(f'{k}="{env_value(v)}"' for k, v in sorted((labels or {}).items()))
    return f"""FROM {base_image}
USER root
# Python + agent tools for mucli, via whichever package manager the base uses. Bases that already
# have Python 3.10+ with venv, git and patch (container-mode environments do) skip this step.
RUN set -e; \\
    if python3 -c 'import sys, venv; assert sys.version_info >= (3, 10)' 2>/dev/null \\
        && command -v git >/dev/null 2>&1 && command -v patch >/dev/null 2>&1; then \\
        echo "mucli toolchain image: using the base image's Python"; \\
    elif command -v apt-get >/dev/null 2>&1; then \\
        apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
            python3 python3-venv python3-pip git patch ripgrep ca-certificates && rm -rf /var/lib/apt/lists/*; \\
    elif command -v apk >/dev/null 2>&1; then \\
        apk add --no-cache python3 py3-pip git patch ripgrep ca-certificates; \\
    elif command -v dnf >/dev/null 2>&1; then \\
        dnf install -y python3 python3-pip git patch ripgrep ca-certificates && dnf clean all; \\
    elif command -v microdnf >/dev/null 2>&1; then \\
        microdnf install -y python3 python3-pip git patch ca-certificates && microdnf clean all; \\
    elif command -v yum >/dev/null 2>&1; then \\
        yum install -y python3 python3-pip git patch ca-certificates && yum clean all; \\
    else echo "mucli toolchain image: no supported package manager (apt/apk/dnf/yum) in {base_image}" >&2; exit 1; fi; \\
    python3 -c 'import sys; assert sys.version_info >= (3, 10), "mucli needs Python 3.10+ in the base image"'
COPY requirements.txt /opt/mucli/requirements.txt
# A base that already carries mucli's requirements (container mode) lends them to the venv.
RUN if python3 -c 'import fastapi, httpx, tiktoken' 2>/dev/null; \\
        then python3 -m venv --system-site-packages /opt/mucli-venv; else python3 -m venv /opt/mucli-venv; fi \\
    && /opt/mucli-venv/bin/pip install --no-cache-dir -r /opt/mucli/requirements.txt \\
    && mkdir -p /opt/mucli/.cache/tiktoken \\
    && TIKTOKEN_CACHE_DIR=/opt/mucli/.cache/tiktoken /opt/mucli-venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
COPY . /opt/mucli
# Toolchains installed under /root (rustup's default) must stay usable as the host user.
RUN chmod -R a+rX /root /opt/mucli 2>/dev/null || true; mkdir -p /cache && chmod 1777 /cache
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/mucli TIKTOKEN_CACHE_DIR=/opt/mucli/.cache/tiktoken
ENV {neutral}
{env_lines}LABEL io.mucli.toolchain-base="{base_image}" io.mucli.python="{VENV_PYTHON}"{(" " + label_line) if label_line else ""}
WORKDIR /workspace
ENTRYPOINT []
CMD []
"""


def probe_env(base_image: str, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Dict[str, str]:
    """Toolchain homes from the base image, so they survive HOME=/tmp and a non-root user."""
    script = ("for v in " + " ".join(PROBED_HOMES) + "; do eval \"x=\\${$v}\"; [ -n \"$x\" ] && echo \"$v=$x\"; done; "
              "[ -z \"$RUSTUP_HOME\" ] && [ -d /root/.rustup ] && echo RUSTUP_HOME=/root/.rustup; "
              "[ -z \"$RUSTUP_HOME\" ] && [ -d /usr/local/rustup ] && echo RUSTUP_HOME=/usr/local/rustup; "
              "echo PATH=$PATH; true")
    from .cli import container_cli
    proc = run([container_cli(), "run", "--rm", "--network", "none", "--entrypoint", "sh", base_image, "-c", script],
               capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"cannot start base image {base_image}: {(proc.stderr or proc.stdout).strip()[:400]}")
    env: Dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        if key in (*PROBED_HOMES, "PATH") and value and '"' not in value:
            env.setdefault(key, value)
    return env


# ------------------------------------------------------------------ build state (per process)
_lock = threading.Lock()
_builds: Dict[str, Dict[str, Any]] = {}


def image_exists(tag: str, run=subprocess.run) -> bool:
    try:
        from .cli import container_cli
        return run([container_cli(), "image", "inspect", tag], capture_output=True, text=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def build_image(base_image: str, *, run=subprocess.run, log: Optional[Callable[[str], None]] = None,
                source: Optional[Path] = None, tag: Optional[str] = None,
                labels: Optional[Dict[str, str]] = None) -> str:
    """Build (or reuse) the derived image for ``base_image``. Returns its tag.

    ``tag``/``labels`` name a job-scoped image (build environments): it is built once for
    that job and carries the job's ``io.mucli.job`` label so cleanup can find it."""
    if not IMAGE_RE.fullmatch(base_image) or base_image.startswith("-"):
        raise ValueError(f"invalid base image: {base_image!r}")
    source = source or source_root()
    tag = tag or derived_tag(base_image, source)
    if image_exists(tag, run):
        return tag
    with _lock:
        state = _builds.setdefault(tag, {"base_image": base_image, "tag": tag})
        if state.get("status") == "building":
            raise RuntimeError(f"toolchain image {tag} is already being built")
        state.update(status="building", started_at=time.time(), log="", error="")
    emit = log or (lambda text: None)

    def note(text: str) -> None:
        with _lock:
            state["log"] = (state.get("log", "") + text)[-65536:]
        emit(text)

    try:
        note(f"Probing {base_image} …\n")
        env = probe_env(base_image, run)
        from .build import prepare_context
        with tempfile.TemporaryDirectory(prefix="mucli-toolchain-build-") as tmp:
            prepare_context(source, Path(tmp))
            Path(tmp, "Dockerfile").write_text(dockerfile(base_image, env, labels))
            note(f"Building {tag} …\n")
            from .cli import container_cli
            cli = container_cli()
            proc = subprocess.Popen([cli, "build", "--pull=false", "-t", tag, tmp], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True) if run is subprocess.run else None
            if proc is None:  # injected runner (tests)
                result = run([cli, "build", "--pull=false", "-t", tag, tmp], capture_output=True, text=True, timeout=7200)
                note(result.stdout or "")
                if result.returncode != 0:
                    raise RuntimeError(f"docker build failed: {(result.stderr or result.stdout).strip()[-600:]}")
            else:
                for line in proc.stdout:
                    note(line)
                if proc.wait() != 0:
                    raise RuntimeError(f"docker build failed for {tag}; see the build log")
        with _lock:
            state.update(status="built", finished_at=time.time())
        note(f"Built {tag}\n")
        return tag
    except Exception as exc:
        with _lock:
            state.update(status="failed", error=str(exc)[:1000], finished_at=time.time())
        raise


def build_in_background(base_image: str) -> Dict[str, Any]:
    tag = derived_tag(base_image)
    with _lock:
        state = _builds.get(tag)
        if state and state.get("status") == "building":
            return dict(state)
    thread = threading.Thread(target=lambda: _quiet(build_image, base_image), name="mucli-toolchain-build", daemon=True)
    thread.start()
    time.sleep(0.05)
    return status(base_image)


def _quiet(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - recorded in the build state
        pass


def status(base_image: str) -> Dict[str, Any]:
    tag = derived_tag(base_image)
    with _lock:
        state = dict(_builds.get(tag) or {})
    if state.get("status") != "building":
        state["status"] = "built" if image_exists(tag) else state.get("status") or "missing"
    state.update(base_image=base_image, tag=tag)
    state["log_tail"] = str(state.pop("log", ""))[-8000:]
    return state


def toolchains_status() -> List[Dict[str, Any]]:
    return [{**row, **status(row["base_image"])} for row in toolchain_rows()]


def cache_dir(job: Any) -> Path:
    """Host-owned per-job cache, removed with the job's artifacts."""
    from ..core.artifacts import artifacts_for
    path = Path(artifacts_for(job).path("cache"))
    path.mkdir(parents=True, exist_ok=True)
    return path



def binding_cache_dir(job: Any, binding: str) -> Path:
    """Per-job, per-build-environment cache (``cache/<binding>``), removed with the job's artifacts."""
    from ..core.artifacts import artifacts_for
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(binding or "default")).strip("-.") or "default"
    path = Path(artifacts_for(job).path("cache", safe))
    path.mkdir(parents=True, exist_ok=True)
    return path
