"""Job build environments: per-job snapshots of container-mode environments.

Design: ``documentation/plans/job_build_environments.md``.

A job resolves its *environment set* once, before its first sandboxed stage: one or many
*bindings* ``{name, source: environment|template|image, ref, paths, primary, docker, gpus}``.
Each binding is snapshotted into images owned by that job alone (labelled
``io.mucli.job=<job id>``): the job uses them for every stage, fix attempt and retry, and
they are removed when the job ends (or after idle retention unless the job is kept).

Snapshot of a container-mode environment:

1. ``docker commit --pause=true`` with the secret-env scrub (``MUCLI_CONTAINER_MODE`` and
   ``MUCLI_WORKER_*`` included, so the sandbox worker never thinks it is in container mode).
2. ``/root``: when it is a named volume, a filtered tar of it is added; when it lives in the
   container layer (container mode keeps only ``/root/.mucli`` in a volume) the excluded
   paths (secrets, mucli state, shell history, caches) are deleted in the snapshot, so stage
   containers never see them.
3. The login shell's environment (``bash -lic 'env -0'``) is probed and its PATH and
   toolchain homes are baked in, so ``.bashrc``/``.profile`` PATH edits survive.
4. mucli's job layer (venv + tools + ``/cache``) is added by ``toolchain.build_image``.
5. A secret scan of ``/root``, ``/home`` and ``/etc`` blocks the job on any hit.
"""
from __future__ import annotations

import fcntl
import fnmatch
import json
import os
import posixpath
import re
import shlex
import subprocess
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .runner import StagePaused

LABEL_JOB = "io.mucli.job"
LABEL_BINDING = "io.mucli.binding"
LABEL_SOURCE = "io.mucli.source"
LABEL_ROLE = "io.mucli.role"
LABEL_HOME = "io.mucli.home"
SOURCES = ("environment", "template", "image")
_PREFIXES = {"env": "environment", "environment": "environment", "template": "template", "image": "image"}
NAME_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,39}")
REF_RE = re.compile(r"[A-Za-z0-9][\w.-]{0,127}")
MAX_PARALLEL_SNAPSHOTS = 3

# Kept from the environment's login shell (plus PATH and Settings → probe_env_extra).
PROBE_KEYS = ("CARGO_HOME", "RUSTUP_HOME", "NVM_DIR", "PYENV_ROOT", "GOROOT", "GOPATH", "JAVA_HOME",
              "ANDROID_HOME", "DOTNET_ROOT", "CONDA_PREFIX", "LD_LIBRARY_PATH", "PKG_CONFIG_PATH")

# Never copied from /root into a job snapshot. Paths are relative to /root; globs allowed.
HOME_SECRET_EXCLUDES = (".ssh", ".gnupg", ".aws", ".azure", ".config/gcloud", ".config/gh", ".kube", ".netrc",
                        ".git-credentials", ".docker/config.json", ".npmrc", ".pypirc", ".cargo/credentials*")
HOME_STATE_EXCLUDES = (".mucli",)
HOME_HISTORY_EXCLUDES = (".*_history",)
# Caches go to the per-job /cache mount instead.
HOME_CACHE_EXCLUDES = (".cache", ".cargo/registry", ".cargo/git", ".npm/_cacache", "go/pkg/mod")
# Shell start-up files often export tokens (mu.security.secret_paths denies them); the probe
# already captured the PATH edits they make, so job snapshots drop them.
HOME_RC_EXCLUDES = (".bashrc", ".zshrc", ".profile", ".bash_profile", ".zprofile")

# Secret scan: credential directories and files under any home directory.
_SECRET_HOME_DIRS = (".ssh", ".gnupg", ".aws", ".azure", ".config/gcloud", ".config/gh", ".kube", ".cargo/credentials.d")
_SECRET_HOME_FILES = (".netrc", ".git-credentials", ".docker/config.json", ".npmrc", ".pypirc",
                      ".cargo/credentials", ".cargo/credentials.toml")
_SECRET_BASENAMES = ("id_rsa", "id_rsa.*", "id_ed25519", "id_ed25519.*", "id_ecdsa", "id_ecdsa.*", "id_dsa", "id_dsa.*",
                     "*.pem", "*.key", "*.pfx", "*.p12", "*.jks", "*.keystore", ".env", ".env.*",
                     "credentials*.json", "service-account*.json", "service_account*.json", "gcp-key*.json")
# System trust stores and package trees ship certificates and test keys; they are not user secrets.
SCAN_IGNORE = ("/etc/ssl/*", "/etc/pki/*", "/etc/ca-certificates/*", "/etc/ssh/ssh_config", "/etc/ssh/ssh_config.d/*",
               "/etc/ssh/sshd_config", "/etc/ssh/sshd_config.d/*", "/etc/ssh/moduli", "*.pub",
               "*/site-packages/*", "*/dist-packages/*", "*/node_modules/*", "/root/.rustup/*",
               "/root/.nvm/versions/*", "/root/.sdkman/candidates/*", "/root/.pyenv/versions/*",
               "/root/.local/lib/*", "/root/go/pkg/*", "/root/.cargo/registry/*", "/root/.cargo/git/*")
_SCAN_PRUNE = ("node_modules", "site-packages", "dist-packages", ".rustup")

# Model providers are reached only through the stage relay, never via inherited egress.
MODEL_HOSTS = ("api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com",
               "aiplatform.googleapis.com", "ollama.com", "openaipublic.blob.core.windows.net")


def home_id() -> str:
    """Identifies this mucli home (``MUCLI_HOME``) on a shared Docker daemon, so one home's
    sweeper never removes another home's job resources."""
    import hashlib
    root = os.path.realpath(os.path.expanduser(os.environ.get("MUCLI_HOME") or "~/.mucli/"))
    return hashlib.sha1(root.encode()).hexdigest()[:12]


def job_labels(job_id: str) -> Dict[str, str]:
    return {LABEL_JOB: str(job_id), LABEL_HOME: home_id()}


class EnvironmentBlocked(StagePaused):
    """A build environment cannot be prepared; the job parks (needs input) with this message."""


# ------------------------------------------------------------------ bindings


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "y")


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_.-]+", "-", str(text or "").lower()).strip("-._")[:40]
    return slug or "env"


def normalize_paths(values: Any) -> Tuple[str, ...]:
    """Repository-relative component roots; ``.``/blank means the whole repository (``()``)."""
    if values is None:
        return ()
    if isinstance(values, str):
        values = [v for v in re.split(r"[,\n]", values)]
    out: List[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        path = posixpath.normpath(text.replace("\\", "/")).strip("/")
        if text.startswith("/") or path.startswith("..") or "/../" in f"/{path}/":
            raise ValueError(f"build environment path must be repository-relative: {text}")
        if path in ("", "."):
            return ()
        if path not in out:
            out.append(path)
    return tuple(out)


def parse_source(value: str, *, bare: str = "environment") -> Tuple[str, str]:
    """``template:x`` / ``image:x`` / ``environment:x`` (alias ``env:``); a bare name is ``bare``."""
    text = str(value or "").strip()
    prefix, sep, rest = text.partition(":")
    source, ref = (_PREFIXES[prefix.lower()], rest.strip()) if sep and prefix.lower() in _PREFIXES and rest.strip() else (bare, text)
    validate_ref(source, ref)
    return source, ref


def validate_ref(source: str, ref: str) -> None:
    if source not in SOURCES:
        raise ValueError(f"unknown build environment source: {source}")
    if source == "image":
        from .toolchain import IMAGE_RE
        if not ref or not IMAGE_RE.fullmatch(ref) or ref.startswith("-"):
            raise ValueError(f"invalid image reference: {ref!r}")
    elif not REF_RE.fullmatch(ref or ""):
        raise ValueError(f"invalid {source} name: {ref!r}")


def default_name(source: str, ref: str) -> str:
    if source == "environment":
        ref = ref[len("mucli-"):] if ref.startswith("mucli-") else ref
    elif source == "image":
        ref = ref.split("@")[0].rsplit("/", 1)[-1].split(":")[0]
    return _slug(ref)


def normalize_gpus(value: Any) -> Optional[str]:
    """``None`` inherits from the source environment; ``""`` means no GPU."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in ("inherit", "default", ""):
        return None if text.lower() in ("inherit", "default") else ""
    from mu.container.hardware import normalize_gpu_request
    return normalize_gpu_request(text)


@dataclass(frozen=True)
class Binding:
    name: str
    source: str
    ref: str
    paths: Tuple[str, ...] = ()
    primary: bool = False
    docker: bool = False
    gpus: Optional[str] = None
    preload_images: Tuple[str, ...] = ()
    # Repository-declared images (devcontainer.py): the approval fingerprint they need and, for a
    # devcontainer Dockerfile, the build spec ((key, value) pairs; built from the base commit).
    approval: str = ""
    build: Tuple[Tuple[str, str], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        out = {"name": self.name, "source": self.source, "ref": self.ref, "paths": list(self.paths),
               "primary": self.primary, "docker": self.docker, "gpus": self.gpus,
               "preload_images": list(self.preload_images)}
        if self.approval:
            out["approval"] = self.approval
        if self.build:
            out["build"] = [list(pair) for pair in self.build]
        return out

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], *, bare: str = "environment") -> "Binding":
        if not isinstance(raw, dict):
            raise ValueError("a build environment must be an object")
        if raw.get("source") in SOURCES and raw.get("ref"):
            source, ref = str(raw["source"]), str(raw["ref"]).strip()
            validate_ref(source, ref)
        else:
            spec = str(raw.get("env") or raw.get("environment") or raw.get("source") or raw.get("name") or "").strip()
            if not spec:
                raise ValueError("a build environment needs a name (environment, template:<name> or image:<ref>)")
            source, ref = parse_source(spec, bare=bare)
        explicit = str(raw.get("as") or raw.get("binding") or "").strip()
        if not explicit and raw.get("source") in SOURCES and raw.get("name"):
            explicit = str(raw["name"]).strip()
        name = explicit or default_name(source, ref)
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"invalid build environment name: {name!r}")
        preload = raw.get("preload_images") or []
        if isinstance(preload, str):
            preload = [p for p in re.split(r"[,\s]+", preload) if p]
        from .toolchain import IMAGE_RE
        for image in preload:
            if not IMAGE_RE.fullmatch(str(image)) or str(image).startswith("-"):
                raise ValueError(f"invalid preload image: {image!r}")
        return cls(name=name, source=source, ref=ref, paths=normalize_paths(raw.get("paths")),
                   primary=_bool(raw.get("primary")), docker=_bool(raw.get("docker")),
                   gpus=normalize_gpus(raw.get("gpus")), preload_images=tuple(str(p) for p in preload))


def parse_spec(text: str) -> Binding:
    """CLI ``name[:paths][:docker]`` (repeatable ``--env``).

    ``name`` may be ``template:<name>`` or ``image:<ref>`` (an image tag's ``:`` stays part of the
    ref; give paths for an image as ``paths=a,b``). Flags: ``docker``, ``primary``, ``gpus=all``."""
    parts = [p for p in str(text or "").strip().split(":")]
    if not parts or not parts[0].strip():
        raise ValueError("--env needs a build environment name")
    flags: Dict[str, Any] = {}
    while len(parts) > 1:
        last = parts[-1].strip()
        low = last.lower()
        if low in ("docker", "primary"):
            flags[low] = True
        elif low.startswith("gpus="):
            flags["gpus"] = last.split("=", 1)[1]
        elif low.startswith("paths="):
            flags["paths"] = last.split("=", 1)[1]
        else:
            break
        parts.pop()
    head = parts[0].strip().lower()
    if head in _PREFIXES and len(parts) >= 2:
        if _PREFIXES[head] == "image":
            ref = ":".join(parts[1:])
            rest: List[str] = []
        else:
            ref, rest = parts[1], parts[2:]
        spec = f"{_PREFIXES[head]}:{ref}"
    else:
        spec, rest = parts[0], parts[1:]
    if rest:
        if len(rest) > 1 or "paths" in flags:
            raise ValueError(f"cannot parse --env {text!r}; use name[:paths][:docker]")
        flags["paths"] = rest[0]
    return Binding.from_dict({"env": spec, **flags})


def _unique(bindings: Sequence[Binding]) -> List[Binding]:
    out: List[Binding] = []
    seen: set = set()
    for binding in bindings:
        name, n = binding.name, 2
        while name in seen:
            name, n = f"{binding.name[:36]}-{n}", n + 1
        seen.add(name)
        out.append(binding if name == binding.name else Binding(**{**binding.__dict__, "name": name}))
    return out


def bindings_from_list(values: Any, *, bare: str = "environment") -> List[Binding]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("build environments must be a list")
    out = []
    for raw in values:
        if isinstance(raw, str):
            out.append(parse_spec(raw))
        else:
            out.append(Binding.from_dict(raw, bare=bare))
    return _unique(out)


# ------------------------------------------------------------------ resolution (§4)


def _repository(job: Any) -> str:
    meta = dict(getattr(job, "metadata", None) or {})
    return str(meta.get("repository_root") or getattr(job, "repository", "") or "")


def repo_config(repo: str) -> Dict[str, Any]:
    path = Path(repo) / ".mucli" / "jobs.toml"
    if not repo or not path.is_file():
        return {}
    try:
        import tomllib
    except ImportError:  # Python 3.10
        import tomli as tomllib
    try:
        return tomllib.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f".mucli/jobs.toml is not valid TOML: {exc}") from exc


def session_environment(session_name: str) -> str:
    """The container-mode environment a saved session is attached to ('' when none)."""
    from utils import config as _config
    name = str(session_name or "").strip()
    if not name or "/" in name or name.startswith("."):
        return ""
    path = os.path.join(_config.HISTORY_DIR, "sessions", name, "session.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ""
    return str((dict(data.get("container_config") or {})).get("container_name") or "")


def toolchain_bindings(job: Any, settings: Optional[Dict[str, Any]] = None) -> List[Binding]:
    """Settings → Toolchains rows for the job's repository (longest repository root wins; a
    repository may have several rows mapping components to different sources)."""
    from .toolchain import job_repository, toolchain_rows
    repo = os.path.realpath(job_repository(job)) if job_repository(job) else ""
    if not repo:
        return []
    best, rows = "", []
    for row in toolchain_rows(settings):
        root = os.path.realpath(os.path.expanduser(str(row["repository"]).strip()))
        if repo == root or repo.startswith(root.rstrip("/") + "/"):
            if len(root) > len(best):
                best, rows = root, [row]
            elif root == best:
                rows.append(row)
    out = []
    for row in rows:
        out.append(Binding.from_dict({"env": str(row["base_image"]).strip(), "paths": row.get("paths") or "",
                                      "docker": row.get("docker") or "", "gpus": row.get("gpus") if str(row.get("gpus") or "").strip() else None},
                                     bare="image"))
    return _unique(out)


def is_cloned_repository(job: Any) -> bool:
    """A repository mucli cloned from a URL (``metadata.repository_url``, or a checkout under
    ``$MUCLI_HOME/repos``): its contents are untrusted remote input, unlike the user's own
    local repositories."""
    if str((getattr(job, "metadata", None) or {}).get("repository_url") or "").strip():
        return True
    repo = _repository(job)
    if not repo:
        return False
    from ..clone import repos_root
    root = os.path.realpath(repos_root())
    path = os.path.realpath(repo)
    return path == root or path.startswith(root.rstrip("/") + "/")


ENVIRONMENTS_FP_PREFIX = "environment:"


def environments_fingerprint(bindings: Sequence[Binding]) -> str:
    """What the user approves for a cloned repository's ``environments``: the exact resolved
    bindings (source, ref, paths, docker, GPUs, preload images)."""
    import hashlib
    rows = [{k: v for k, v in b.to_dict().items() if k not in ("approval", "build")} for b in bindings]
    return ENVIRONMENTS_FP_PREFIX + hashlib.sha1(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:20]


def resolve_bindings(job: Any, settings: Optional[Dict[str, Any]] = None) -> Tuple[List[Binding], str]:
    """First source that yields bindings wins: job override → repo config → session → settings → default."""
    if settings is None:
        from ..job_settings import load_settings
        settings = load_settings()
    from ..job_settings import rule
    override = (getattr(job, "execution", None) or {}).get("environments")
    if override:
        return bindings_from_list(override), "job"
    if is_cloned_repository(job):
        # A cloned repository's jobs.toml is remote content: read it from the base commit and
        # require the user's approval of exactly the bindings it resolves to.
        from .repo_config import base_config
        configured = base_config(job).get("environments")
        if configured:
            bindings = bindings_from_list(configured)
            fp = environments_fingerprint(bindings)
            return [replace(b, approval=fp) for b in bindings], "repo"
    else:
        configured = repo_config(_repository(job)).get("environments")
        if configured:
            return bindings_from_list(configured), "repo"
    declared = repository_binding(job)
    if declared is not None:
        return declared
    if rule("environments.use_session_environment", settings):
        container = session_environment(str((getattr(job, "metadata", None) or {}).get("origin_session") or ""))
        if container:
            return [Binding(name=default_name("environment", container), source="environment", ref=container)], "session"
    rows = toolchain_bindings(job, settings)
    if rows:
        return rows, "settings"
    implicit = repository_binding(job, implicit_devcontainer=True)
    if implicit is not None and implicit[0] and implicit[0][0].approval:
        from .devcontainer import is_approved
        from .toolchain import job_repository
        if is_approved(job_repository(job), implicit[0][0].approval):
            return implicit
    return [], "default"


def repository_binding(job: Any, *, implicit_devcontainer: bool = False) -> Optional[Tuple[List[Binding], str]]:
    """The base commit's ``[sandbox] image`` (or devcontainer) as a binding needing approval.

    Raises ``ValueError`` for an explicit but unusable declaration; an unusable implicit
    devcontainer is ignored."""
    from .devcontainer import repository_environment
    env = repository_environment(job, implicit_devcontainer=implicit_devcontainer)
    if env is None:
        return None
    if env.get("error"):
        if env.get("origin") == "repo-image" or env.get("explicit"):
            raise ValueError(env["error"])
        return None
    build: Tuple[Tuple[str, str], ...] = ()
    if env["kind"] == "build":
        build = (("spec", json.dumps(env["spec"], sort_keys=True)), ("fingerprint", env["fingerprint"]))
    name = "devcontainer" if env["origin"] == "devcontainer" else default_name("image", env["ref"])
    return [Binding(name=name, source="image", ref=env["ref"], approval=env["fingerprint"], build=build)], env["origin"]


def _paths_of(binding: Any) -> Tuple[str, ...]:
    return tuple(binding.get("paths") or ()) if isinstance(binding, dict) else tuple(binding.paths)


def _name_of(binding: Any) -> str:
    return str(binding.get("name") if isinstance(binding, dict) else binding.name)


def binding_for_path(bindings: Sequence[Any], path: str, *, fallback: bool = True) -> Optional[Any]:
    """The binding owning ``path``: longest matching component root, else a whole-repo binding."""
    rel = posixpath.normpath(str(path or "").replace("\\", "/")).strip("/")
    best, best_len = None, -1
    for binding in bindings:
        for root in _paths_of(binding):
            if (rel == root or rel.startswith(root + "/")) and len(root) > best_len:
                best, best_len = binding, len(root)
    if best is not None or not fallback:
        return best
    return next((b for b in bindings if not _paths_of(b)), None)


def primary_binding(bindings: Sequence[Any], files: Iterable[str] = ()) -> Optional[Any]:
    """Agent stages run in the binding marked primary, else the one owning most planned files."""
    if not bindings:
        return None
    marked = [b for b in bindings if (b.get("primary") if isinstance(b, dict) else b.primary)]
    if marked:
        return marked[0]
    if len(bindings) == 1:
        return bindings[0]
    scores = {_name_of(b): 0 for b in bindings}
    for path in files or ():
        owner = binding_for_path(bindings, path)
        if owner is not None:
            scores[_name_of(owner)] += 1
    best = max(bindings, key=lambda b: (scores[_name_of(b)], not _paths_of(b)))
    if scores[_name_of(best)] == 0:
        return next((b for b in bindings if not _paths_of(b)), bindings[0])
    return best


def binding_for_suite(bindings: Sequence[Any], suite: Any, files: Iterable[str] = ()) -> Optional[Any]:
    """Explicit ``env`` → the suite's ``cwd`` → a path its command names → the primary binding."""
    if not bindings:
        return None
    env = str(getattr(suite, "env", "") or "").strip()
    if env:
        match = next((b for b in bindings if _name_of(b) == env), None)
        if match is None:
            raise ValueError(f"check suite `{getattr(suite, 'name', '')}` names build environment `{env}`, "
                             f"which this job doesn't have ({', '.join(_name_of(b) for b in bindings)})")
        return match
    cwd = str(getattr(suite, "cwd", "") or "").strip()
    if cwd:
        owner = binding_for_path(bindings, cwd)
        if owner is not None:
            return owner
    try:
        tokens = shlex.split(str(getattr(suite, "command", "") or ""))
    except ValueError:
        tokens = []
    for token in tokens[1:]:
        candidate = token.split("=", 1)[1] if token.startswith("-") and "=" in token else token
        if candidate.startswith("-") or not candidate:
            continue
        owner = binding_for_path(bindings, candidate, fallback=False)
        if owner is not None:
            return owner
    return primary_binding(bindings, files)


# ------------------------------------------------------------------ docker access


class Docker:
    """Thin, injectable docker CLI access (tests substitute ``run``/``popen``)."""

    def __init__(self, runner=None, *, keep_ledger: bool = False):
        from mu.container.docker_cli import CommandRunner
        self.runner = runner or CommandRunner()
        self.keep_ledger = keep_ledger

    @property
    def binary(self) -> str:
        from .cli import container_cli
        return self.runner.require(container_cli())

    def run(self, *args: str, check: bool = True, timeout: Optional[float] = 600, input_text: Optional[str] = None):
        result = self.runner.run([self.binary, *args], check=check, timeout=timeout, input_text=input_text)
        if not self.keep_ledger:
            self.runner.commands.clear()  # diagnostics ledger; keep it bounded in long-lived processes
        return result

    def popen(self, args: Sequence[str], **kwargs) -> subprocess.Popen:
        return subprocess.Popen([self.binary, *args], **kwargs)

    def exists(self, kind: str, ref: str) -> bool:
        try:
            return self.run(kind, "inspect", ref, check=False, timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def inspect(self, kind: str, ref: str, fmt: str) -> str:
        result = self.run(kind, "inspect", "--format", fmt, ref, check=False, timeout=30)
        return (result.stdout or "").strip() if result.returncode == 0 else ""


# ------------------------------------------------------------------ home + secrets


def _relative(name: str) -> str:
    """Tar/listing member name relative to its root (``./.ssh/x`` → ``.ssh/x``; leading dots kept)."""
    text = str(name or "")
    while text.startswith("./"):
        text = text[2:]
    return posixpath.normpath(text.lstrip("/")).strip("/") if text not in ("", ".") else ""


def _glob_hit(rel: str, pattern: str) -> bool:
    pattern = pattern.strip().strip("/")
    if not pattern:
        return False
    if rel == pattern or rel.startswith(pattern + "/"):
        return True
    if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(posixpath.basename(rel), pattern):
        return True
    # A glob naming a directory excludes its subtree.
    parts = rel.split("/")
    return any(fnmatch.fnmatch("/".join(parts[:i]), pattern) for i in range(1, len(parts)))


def home_excluded(rel: str, extra: Sequence[str] = (), *, strict: bool = True) -> Optional[str]:
    """Why ``/root/<rel>`` stays out of a snapshot (``None`` = copied).

    ``strict`` (job snapshots) also drops caches and shell start-up files; templates keep them."""
    rel = _relative(rel)
    if not rel or rel == ".":
        return None
    groups = [("secret", HOME_SECRET_EXCLUDES), ("mucli state", HOME_STATE_EXCLUDES),
              ("shell history", HOME_HISTORY_EXCLUDES)]
    if strict:
        groups += [("cache", HOME_CACHE_EXCLUDES), ("shell start-up file", HOME_RC_EXCLUDES)]
    groups.append(("excluded in Settings", tuple(p.strip().removeprefix("/root/").removeprefix("~/") for p in extra or ())))
    for reason, patterns in groups:
        for pattern in patterns:
            if reason == "shell history":
                if any(fnmatch.fnmatch(part, pattern) for part in rel.split("/")):
                    return reason
            elif _glob_hit(rel, pattern):
                return reason
    secret = image_secret_reason("/root/" + rel)
    return f"secret ({secret})" if secret else None


def image_secret_reason(path: str, allow: Sequence[str] = ()) -> Optional[str]:
    """Secret material at ``path`` inside an image (``None`` when the path is fine)."""
    p = posixpath.normpath("/" + str(path or "").lstrip("/"))
    for pattern in allow or ():
        pattern = str(pattern).strip()
        if pattern and (p == pattern.rstrip("/") or p.startswith(pattern.rstrip("/") + "/") or fnmatch.fnmatch(p, pattern)):
            return None
    if any(fnmatch.fnmatch(p, pattern) for pattern in SCAN_IGNORE):
        return None
    base = posixpath.basename(p)
    home = ""
    if p == "/root" or p.startswith("/root/"):
        home = "/root"
    else:
        match = re.match(r"^(/home/[^/]+)(?:/|$)", p)
        home = match.group(1) if match else ""
    if home and p != home:
        rel = p[len(home) + 1:]
        for directory in _SECRET_HOME_DIRS:
            if rel == directory or rel.startswith(directory + "/"):
                return f"credentials directory ~/{directory}"
        if rel in _SECRET_HOME_FILES:
            return f"credentials file ~/{rel}"
        if fnmatch.fnmatch(base, ".*_history"):
            return "shell history"
    if p.startswith("/etc/ssh/") and base != "ssh":
        return "SSH key material"
    from .policy import is_env_template
    for pattern in _SECRET_BASENAMES:
        if fnmatch.fnmatch(base, pattern):
            if pattern.startswith(".env") and is_env_template(base):
                return None
            return f"secret file pattern {pattern}"
    return None


def filter_home_tar(src, dst, extra: Sequence[str] = (), *, strict: bool = True) -> Tuple[int, List[str]]:
    """Copy a tar of ``/root`` minus excluded members. Streams; nothing is extracted on the host."""
    kept_bytes, skipped, excluded_dirs = 0, [], []
    with tarfile.open(fileobj=src, mode="r|*") as source, tarfile.open(fileobj=dst, mode="w|") as target:
        for member in source:
            rel = _relative(member.name)
            if not rel or rel == ".":
                continue
            if any(rel == d or rel.startswith(d + "/") for d in excluded_dirs):
                continue
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                continue  # devices and fifos never belong in a toolchain home
            reason = home_excluded(rel, extra, strict=strict)
            if reason is None and member.islnk():
                link = _relative(member.linkname)
                reason = home_excluded(link, extra, strict=strict)
            if reason is not None:
                skipped.append(f"{rel} ({reason})")
                if member.isdir():
                    excluded_dirs.append(rel)
                continue
            member.name = rel
            if member.isfile():
                target.addfile(member, source.extractfile(member))
                kept_bytes += int(member.size or 0)
            else:
                target.addfile(member)
    return kept_bytes, skipped


def topmost(paths: Iterable[str]) -> List[str]:
    out: List[str] = []
    for path in sorted(set(paths)):
        if not any(path == p or path.startswith(p.rstrip("/") + "/") for p in out):
            out.append(path)
    return out


def _list_files(docker: Docker, image: str, roots: Sequence[str], *, prune: Sequence[str] = ()) -> List[str]:
    clauses = " ".join(f"-name {shlex.quote(n)} -prune -o" for n in prune)
    script = f"find {' '.join(shlex.quote(r) for r in roots)} -xdev {clauses} -print 2>/dev/null; true"
    result = docker.run("run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "sh", image, "-c", script,
                        check=False, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"cannot list files in {image}: {(result.stderr or '').strip()[-400:]}")
    return [line for line in (result.stdout or "").splitlines() if line.startswith("/")]


def secret_scan(docker: Docker, image: str, allow: Sequence[str] = ()) -> List[str]:
    """Paths under /root, /home and /etc of ``image`` that look like secrets."""
    hits = []
    for path in _list_files(docker, image, ("/root", "/home", "/etc"), prune=_SCAN_PRUNE):
        reason = image_secret_reason(path, allow)
        if reason:
            hits.append(f"{path} ({reason})")
    return hits


def capture_home_volume(docker: Docker, volume: str, image: str, dest: Path, extra: Sequence[str],
                        *, strict: bool = True) -> Tuple[int, List[str]]:
    """Filtered tar of a ``/root`` named volume, read through a throwaway helper container."""
    proc = docker.popen(["run", "--rm", "--network", "none", "--user", "0",
                         "--mount", f"type=volume,src={volume},dst=/src,readonly",
                         "--entrypoint", "tar", image, "-C", "/src", "-cf", "-", "."],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with open(dest, "wb") as out:
            result = filter_home_tar(proc.stdout, out, extra, strict=strict)
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.wait(timeout=600) != 0:
            raise RuntimeError(f"cannot read the home volume {volume}: {stderr.strip()[-400:]}")
        return result
    finally:
        if proc.poll() is None:
            proc.kill()


def parse_probe(text: str, keys: Iterable[str], *, nul: bool = True) -> Dict[str, str]:
    wanted = set(keys) | {"PATH"}
    env: Dict[str, str] = {}
    for entry in (text or "").split("\0" if nul else "\n"):
        key, sep, value = entry.partition("=")
        key = key.strip()
        if not sep or key not in wanted or not value or '"' in value or "\n" in value:
            continue
        env.setdefault(key, value)
    return env


def probe_shell_env(docker: Docker, image: str, extra_keys: Sequence[str] = (), *, volume: str = "") -> Dict[str, str]:
    """PATH and toolchain homes from the environment's login shell (captures .bashrc/.profile edits)."""
    keys = (*PROBE_KEYS, *extra_keys)
    mount = ["--mount", f"type=volume,src={volume},dst=/root,readonly"] if volume else []
    base = ["run", "--rm", "--network", "none", "--user", "0", *mount, "-e", "HOME=/root"]
    try:
        result = docker.run(*base, "--entrypoint", "bash", image, "-lic", "env -0", check=False, timeout=120)
        if result.returncode == 0 and "PATH=" in (result.stdout or ""):
            return parse_probe(result.stdout, keys, nul=True)
        result = docker.run(*base, "--entrypoint", "sh", image, "-lc", "env", check=False, timeout=120)
        if result.returncode == 0:
            return parse_probe(result.stdout, keys, nul=False)
    except subprocess.TimeoutExpired:
        pass
    return {}


def filter_model_hosts(domains: Iterable[str]) -> List[str]:
    """Drop model-provider hosts (and any wildcard/parent covering one) from inherited egress."""
    hosts = list(MODEL_HOSTS)
    ollama = os.environ.get("OLLAMA_HOST", "")
    if ollama:
        from urllib.parse import urlparse
        host = urlparse(ollama if "://" in ollama else f"http://{ollama}").hostname
        if host:
            hosts.append(host)
    out = []
    for raw in domains or ():
        domain = str(raw or "").strip().lower()
        bare = domain[2:] if domain.startswith("*.") else domain
        if not bare:
            continue
        if any(h == bare or h.endswith("." + bare) or bare.endswith("." + h) for h in hosts):
            continue
        if domain not in out:
            out.append(domain)
    return out


# ------------------------------------------------------------------ snapshot pipeline (§5)


def job12(job_id: str) -> str:
    return str(job_id)[:12]


def _labels(job_id: str, binding: Dict[str, Any], role: str) -> Dict[str, str]:
    return {**job_labels(job_id), LABEL_BINDING: binding["name"], LABEL_SOURCE: f"{binding['source']}:{binding['ref']}",
            LABEL_ROLE: role}


def snapshot_scrub_env() -> Tuple[str, ...]:
    from mu.container.supervisor import SNAPSHOT_SCRUB_ENV
    from .toolchain import NEUTRALISED_ENV
    return tuple(dict.fromkeys((*SNAPSHOT_SCRUB_ENV, *NEUTRALISED_ENV)))


def _env_lines(env: Dict[str, str]) -> str:
    from .toolchain import env_value
    return "".join(f'ENV {k}="{env_value(v)}"\n' for k, v in sorted(env.items()))


def _label_line(labels: Dict[str, str]) -> str:
    from .toolchain import env_value
    return "LABEL " + " ".join(f'{k}="{env_value(v)}"' for k, v in sorted(labels.items())) + "\n"


def build_home_snapshot(docker: Docker, container: str, base_image_tag: str, tag: str, labels: Dict[str, str], *,
                        root_volume: str = "", extra_excludes: Sequence[str] = (), probe_keys: Sequence[str] = (),
                        strict: bool = True, scrub: Sequence[str] = (), probe: bool = True,
                        log: Callable[[str], None] = lambda text: None) -> Dict[str, Any]:
    """Commit ``container`` and build ``tag`` from it with ``/root`` filtered (shared with templates).

    Returns ``{probed_env, home_bytes, skipped_paths}``. The commit tag ``base_image_tag`` is removed."""
    commit = ["commit", "--pause=true"]
    for key in scrub:
        commit += ["--change", f"ENV {key}="]
    docker.run(*commit, container, base_image_tag, timeout=1800)
    try:
        info: Dict[str, Any] = {"probed_env": {}, "home_bytes": 0, "skipped_paths": []}
        with tempfile.TemporaryDirectory(prefix="mucli-env-snapshot-") as tmp:
            lines = [f"FROM {base_image_tag}\n", "USER root\n"]
            if root_volume:
                log("Capturing the /root volume …\n")
                info["home_bytes"], info["skipped_paths"] = capture_home_volume(
                    docker, root_volume, base_image_tag, Path(tmp) / "home.tar", extra_excludes, strict=strict)
                lines.append("ADD home.tar /root/\n")
            else:
                Path(tmp, "home.tar").write_bytes(b"")
            # Excluded paths that live in the committed layer are deleted, so no stage sees them.
            listed = _list_files(docker, base_image_tag, ("/root",))
            removed = topmost(p for p in listed if p != "/root" and home_excluded(p[len("/root/"):], extra_excludes, strict=strict))
            for path in removed:
                info["skipped_paths"].append(f"{path[len('/root/'):]} ({home_excluded(path[len('/root/'):], extra_excludes, strict=strict)})")
            if removed:
                lines.append("RUN " + json.dumps(["rm", "-rf", "--", *removed]) + "\n")
            if probe:
                log("Probing the login shell environment …\n")
                info["probed_env"] = probe_shell_env(docker, base_image_tag, probe_keys, volume=root_volume)
                lines.append(_env_lines(info["probed_env"]))
            if strict:  # stages run as the host user
                lines.append("RUN chmod -R a+rX /root 2>/dev/null || true\n")
            lines.append(_label_line(labels))
            Path(tmp, "Dockerfile").write_text("".join(lines))
            log(f"Building {tag} …\n")
            docker.run("build", "--pull=false", "-t", tag, tmp, timeout=3600)
        info["skipped_paths"] = sorted(dict.fromkeys(info["skipped_paths"]))[:500]
        return info
    finally:
        docker.run("image", "rm", base_image_tag, check=False, timeout=120)


def _home_bytes(docker: Docker, image: str) -> int:
    result = docker.run("run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "sh", image, "-c",
                        "du -sk /root 2>/dev/null | cut -f1", check=False, timeout=300)
    try:
        return int((result.stdout or "0").strip().splitlines()[-1]) * 1024
    except (ValueError, IndexError):
        return 0


def _blocked_missing(binding: Dict[str, Any]) -> EnvironmentBlocked:
    return EnvironmentBlocked(f"Build environment `{binding['name']}` ({binding['source']} {binding['ref']}) no longer exists; "
                              "choose another (Change).")


def resolve_source(binding: Dict[str, Any]) -> Dict[str, Any]:
    """Registry facts for a binding's source: container name, image, egress, GPU request."""
    if binding["source"] == "environment":
        from mu.container.supervisor import ContainerSupervisor
        ref = ContainerSupervisor().resolve(binding["ref"])
        if ref is None:
            raise _blocked_missing(binding)
        root_volume = ref.root_volume if str(ref.container_volume or "").rstrip("/") == "/root" else ""
        return {"container": ref.name, "image": ref.image, "egress_allow": list(ref.egress_allow or []),
                "gpus": ref.gpu_request or "", "root_volume": root_volume}
    if binding["source"] == "template":
        from mu.container.templates import TemplateRegistry
        template = TemplateRegistry().get(binding["ref"])
        if template is None:
            raise _blocked_missing(binding)
        return {"image": template.image, "egress_allow": list(template.egress_allow or []), "gpus": "",
                "job_setup": list(template.job_setup or [])}
    return {"image": binding["ref"], "egress_allow": [], "gpus": ""}


def effective_gpus(binding: Dict[str, Any], source: Dict[str, Any], settings: Optional[Dict[str, Any]] = None) -> str:
    from ..job_settings import rule
    if not rule("sandbox.gpus_enabled", settings):
        return ""
    value = binding.get("gpus")
    return str(source.get("gpus") or "") if value is None else str(value or "")


def snapshot_binding(job: Any, binding: Dict[str, Any], *, docker: Docker, settings: Dict[str, Any],
                     log: Callable[[str], None] = lambda text: None) -> Dict[str, Any]:
    """Build one binding's job snapshot + job image; returns its prepared state (§5)."""
    from ..job_settings import rule
    from .toolchain import VENV_PYTHON, build_image
    source = resolve_source(binding)
    stamp = f"{int(time.time())}{os.urandom(2).hex()}"
    slug = _slug(binding["name"])
    snapshot_tag = f"mucli/job-{job12(job.id)}-{slug}:{stamp}"
    runtime_tag = f"mucli/job-{job12(job.id)}-{slug}-runtime:{stamp}"
    state: Dict[str, Any] = {**binding, "status": "preparing", "probed_env": {}, "home_bytes": 0, "skipped_paths": [],
                             "egress_allow": source.get("egress_allow") or [],
                             # The template's job setup as of this snapshot (§4 of job_templates.md).
                             "job_setup": list(source.get("job_setup") or []),
                             "gpus": effective_gpus(binding, source, settings), "python": VENV_PYTHON}
    extra_excludes = list(rule("environments.home_exclude_extra", settings) or [])
    if binding["source"] == "environment":
        if not docker.exists("container", source["container"]):
            raise _blocked_missing(binding)
        log(f"Snapshotting environment {source['container']} (it is paused briefly) …\n")
        info = build_home_snapshot(
            docker, source["container"], f"mucli/job-{job12(job.id)}-{slug}-commit:{stamp}", snapshot_tag,
            _labels(job.id, binding, "job-snapshot"), root_volume=source.get("root_volume") or "",
            extra_excludes=extra_excludes, probe_keys=list(rule("environments.probe_env_extra", settings) or []),
            scrub=snapshot_scrub_env(), log=log)
        state.update(info, snapshot_image=snapshot_tag)
        log(f"Adding mucli's job layer to {snapshot_tag} …\n")
        build_image(snapshot_tag, log=log, tag=runtime_tag, labels=_labels(job.id, binding, "job-image"))
        if not state["home_bytes"]:
            state["home_bytes"] = _home_bytes(docker, runtime_tag)
    else:
        base = source["image"]
        if binding.get("build") and not docker.exists("image", base):
            from .devcontainer import build_args_from, build_devcontainer
            spec, fp = build_args_from(binding)
            from .toolchain import job_repository
            from .devcontainer import is_approved
            if not fp or fp != binding.get("approval") or not is_approved(job_repository(job), fp):
                raise EnvironmentBlocked(f"Build environment `{binding['name']}`: the repository's devcontainer build "
                                         "is not approved.")

            def run(args, **kwargs):  # git on the host; the build through the injectable docker runner
                if args[0] == "git":
                    return subprocess.run(args, **kwargs)
                return docker.runner.run(args, check=False, timeout=kwargs.get("timeout"))
            build_devcontainer(job, spec, base, {LABEL_ROLE: "devcontainer-base", LABEL_HOME: home_id(),
                                                 "io.mucli.fingerprint": fp}, run=run, log=log)
        if not docker.exists("image", base):
            raise EnvironmentBlocked(f"Build environment `{binding['name']}`: image `{base}` is not available locally "
                                     "(job sandboxes never pull). Pull or build it, then retry.")
        log(f"Adding mucli's job layer to {base} …\n")
        derived = build_image(base, log=log)
        with tempfile.TemporaryDirectory(prefix="mucli-env-job-") as tmp:
            Path(tmp, "Dockerfile").write_text(f"FROM {derived}\n" + _label_line(_labels(job.id, binding, "job-image")))
            docker.run("build", "--pull=false", "-t", runtime_tag, tmp, timeout=600)
        state["snapshot_image"] = base
    state["job_image"] = runtime_tag
    allow = list(rule("environments.secret_scan_allow", settings) or [])
    log("Scanning the job image for secrets …\n")
    hits = secret_scan(docker, runtime_tag, allow)
    if hits:
        remove_images(docker, [runtime_tag] + ([snapshot_tag] if binding["source"] == "environment" else []))
        shown = "; ".join(hits[:8]) + (f" (+{len(hits) - 8} more)" if len(hits) > 8 else "")
        error = EnvironmentBlocked(
            f"Build environment `{binding['name']}` contains secret files: {shown}. Remove them from the environment, "
            "or allow specific paths in Settings → Build environments (Allowed secret-scan paths), then retry.")
        error.secret_paths = hits  # type: ignore[attr-defined]
        raise error
    state["digest"] = docker.inspect("image", runtime_tag, "{{.Id}}")
    try:
        state["image_bytes"] = int(docker.inspect("image", runtime_tag, "{{.Size}}") or 0)
    except ValueError:
        state["image_bytes"] = 0
    state.update(status="ready", prepared_at=time.time(), error="")
    return state


def remove_images(docker: Docker, refs: Sequence[str]) -> List[str]:
    """Remove images, children first (several passes for dependent layers). Returns failures."""
    pending = [r for r in dict.fromkeys(refs) if r]
    for _ in range(4):
        failed = [ref for ref in pending
                  if (result := docker.run("image", "rm", "-f", ref, check=False, timeout=180)).returncode != 0
                  and "No such image" not in (result.stderr or "")]
        progress = len(failed) < len(pending)
        pending = failed
        if not pending or not progress:
            break
    return pending


# ------------------------------------------------------------------ job state (metadata)


def _meta(service, job_id: str) -> Dict[str, Any]:
    return dict(service.get(job_id).metadata or {})


def save_meta(service, job_id: str, **updates: Any) -> None:
    meta = _meta(service, job_id)
    for key, value in updates.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
    service.store.update_runtime_fields(job_id, metadata_json=meta)


def job_states(job: Any) -> List[Dict[str, Any]]:
    return [dict(s) for s in (dict(getattr(job, "metadata", None) or {}).get("build_environments") or []) if isinstance(s, dict)]


def _same_set(prepared: Sequence[Dict[str, Any]], wanted: Sequence[Dict[str, Any]]) -> bool:
    def key(state):
        return (state.get("name"), state.get("source"), state.get("ref"), tuple(state.get("paths") or ()),
                bool(state.get("primary")), bool(state.get("docker")), tuple(state.get("preload_images") or ()))
    return [key(s) for s in prepared] == [key(s) for s in wanted]


@contextmanager
def _job_lock(job: Any):
    from ..core.artifacts import artifacts_for
    art = artifacts_for(job).ensure()
    path = Path(art.path("environments.lock"))
    with open(path, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def sysbox_available(docker: Optional[Docker] = None) -> bool:
    from mu.container.hardware import _docker_runtimes
    try:
        return "sysbox-runc" in _docker_runtimes((docker or Docker()).runner)
    except Exception:  # noqa: BLE001 - detection is best effort
        return False


SYSBOX_MESSAGE = ("This job needs Docker-in-Docker, which requires Sysbox. Install it (see Settings → Build environments), "
                  "then retry.")
SYSBOX_GUIDANCE = [
    "Install Sysbox CE for your distribution (https://github.com/nestybox/sysbox/blob/master/docs/user-guide/install-package.md).",
    'Make sure /etc/docker/daemon.json lists the runtime: {"runtimes": {"sysbox-runc": {"path": "/usr/bin/sysbox-runc"}}}.',
    "Restart Docker (sudo systemctl restart docker), then retry the job. mucli never installs Sysbox itself.",
]


PODMAN_DIND_MESSAGE = ("This job needs Docker-in-Docker, but the job sandbox uses Podman (jobs.container_cli), and the "
                       "per-job Sysbox Docker engine is supported only with Docker. Use Docker for this job "
                       "(Settings: jobs.container_cli = docker) or remove `docker` from its build environment.")


def _preflight(states: Sequence[Dict[str, Any]], docker: Docker) -> None:
    if any(s.get("docker") for s in states):
        from .cli import is_podman
        if is_podman():
            raise EnvironmentBlocked(PODMAN_DIND_MESSAGE)
        if not sysbox_available(docker):
            raise EnvironmentBlocked(SYSBOX_MESSAGE)
    for state in states:
        if not state.get("gpus"):
            continue
        from mu.container.docker_cli import ContainerRuntimeError
        from mu.container.hardware import validate_hardware
        try:
            validate_hardware(state["gpus"], runner=docker.runner)
        except (ContainerRuntimeError, ValueError) as exc:
            raise EnvironmentBlocked(f"This job's build environment `{state['name']}` requests GPUs, but the host has no "
                                     f"usable NVIDIA runtime ({exc}).") from exc


def _require_approval(service, job: Any, bindings: Sequence[Binding]) -> None:
    """Repository-declared images/builds park the job until the user approves them."""
    from .devcontainer import approval_message, approval_request, is_approved, repository_environment
    from .toolchain import job_repository
    for binding in bindings:
        if not binding.approval or is_approved(job_repository(job), binding.approval):
            continue
        if binding.approval.startswith(ENVIRONMENTS_FP_PREFIX):
            refs = ", ".join(f"{b.name}={b.source}:{b.ref}" + (" (docker)" if b.docker else "")
                             + (f" (gpus {b.gpus})" if b.gpus else "") for b in bindings)
            env = {"fingerprint": binding.approval, "kind": "environment", "ref": refs,
                   "label": f"the build environments {refs} (.mucli/jobs.toml of a cloned repository)",
                   "origin": "repo-environments"}
        else:
            env = repository_environment(job, implicit_devcontainer=True) or {}
        if env.get("fingerprint") != binding.approval:
            env = {"fingerprint": binding.approval, "kind": "build" if binding.build else "image", "ref": binding.ref,
                   "label": f"image {binding.ref}", "origin": "repo-image"}
        error = EnvironmentBlocked(approval_message(env))
        error.approval = approval_request(job, env)  # type: ignore[attr-defined]
        try:
            service.store.append_event(job.id, "build_environment_approval_required", reason=str(error)[:500],
                                       payload=error.approval)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        raise error


def ensure_environments(service, job: Any, *, docker: Optional[Docker] = None, force: bool = False,
                        settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Resolve and prepare the job's build environments (idempotent; called at every stage start).

    Returns the prepared states (``[]`` = the shared runtime image). Raises ``EnvironmentBlocked``."""
    if settings is None:
        from ..job_settings import load_settings
        settings = load_settings()
    with _job_lock(job):
        states = _ensure_locked(service, job, docker=docker, force=force, settings=settings)
        if states and any(s.get("job_setup") for s in states):
            # Template job setup (job_templates.md §4): once per job image, before the stage.
            from .job_setup import run_job_setup
            run_job_setup(service, service.get(job.id), states,
                          runner=docker.runner if docker is not None else None, settings=settings)
        return states


def _ensure_locked(service, job: Any, *, docker: Optional[Docker], force: bool,
                   settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    job = service.get(job.id)
    meta = dict(job.metadata or {})
    try:
        bindings, origin = resolve_bindings(job, settings)
    except ValueError as exc:
        raise EnvironmentBlocked(f"Build environments are misconfigured: {exc}") from exc
    _require_approval(service, job, bindings)
    prepared = job_states(job)
    if not bindings:
        if prepared:
            cleanup_job(job.id, service=service, docker=docker, reason="environments removed", engine=True)
        if prepared or meta.get("build_environments_source") != "default":
            save_meta(service, job.id, build_environments=[], build_environments_source="default",
                      environments_refresh_requested=None)
        return []
    docker = docker or Docker()
    wanted = [b.to_dict() for b in bindings]
    refresh = bool(meta.get("environments_refresh_requested"))
    if not force and not refresh and prepared and _same_set(prepared, wanted) \
            and all(s.get("status") == "ready" and s.get("job_image") and docker.exists("image", s["job_image"])
                    for s in prepared):
        return prepared
    if prepared and not force and not refresh and _same_set(prepared, wanted):
        service.store.append_event(job.id, "build_environments_recreated",
                                   reason="Build environment images were cleaned while the job sat idle; "
                                          "re-creating them from the current environments.",
                                   payload={"bindings": [s.get("name") for s in prepared]})
    if prepared:
        cleanup_job(job.id, service=None, docker=docker, reason="re-snapshot", containers=False, engine=False)
        if any(b.get("docker") for b in wanted):
            # A fresh engine preloads the new job images (its storage volume is kept).
            from .engine import engine_name
            docker.run("rm", "-f", engine_name(job.id), check=False, timeout=120)
    # Effective GPU requests need the source registry (inherit from the environment).
    try:
        sources = {b["name"]: resolve_source(b) for b in wanted}
    except EnvironmentBlocked as exc:
        save_meta(service, job.id, build_environments=[{**b, "status": "failed", "error": str(exc)} for b in wanted],
                  build_environments_source=origin)
        raise
    states = [{**b, "status": "preparing", "gpus": effective_gpus(b, sources[b["name"]], settings),
               "egress_allow": sources[b["name"]].get("egress_allow") or [],
               "job_setup": list(sources[b["name"]].get("job_setup") or [])} for b in wanted]
    try:
        _preflight(states, docker)
    except EnvironmentBlocked as exc:
        save_meta(service, job.id, build_environments=[{**s, "status": "failed", "error": str(exc)} for s in states],
                  build_environments_source=origin)
        raise
    save_meta(service, job.id, build_environments=states, build_environments_source=origin,
              environments_refresh_requested=None, environments_cleaned_at=None)
    total = len(states)
    service.store.append_event(job.id, "build_environments_preparing",
                               reason=f"Preparing build environments (0/{total})…",
                               payload={"total": total, "source": origin, "bindings": [s["name"] for s in states]})
    done = [0]
    lock = threading.Lock()
    results: Dict[str, Dict[str, Any]] = {}
    errors: Dict[str, BaseException] = {}

    def work(binding: Dict[str, Any]) -> None:
        def log(text: str) -> None:
            if text.strip():
                service.store.append_event(job.id, "build_environment_log",
                                           payload={"binding": binding["name"], "text": text[-2000:]})
        try:
            results[binding["name"]] = snapshot_binding(job, binding, docker=docker, settings=settings, log=log)
        except BaseException as exc:  # noqa: BLE001 - recorded per binding
            errors[binding["name"]] = exc
        with lock:
            done[0] += 1
            service.store.append_event(job.id, "build_environment_progress",
                                       reason=f"Preparing build environments ({done[0]}/{total})…",
                                       payload={"done": done[0], "total": total, "binding": binding["name"],
                                                "ok": binding["name"] in results})

    with ThreadPoolExecutor(max_workers=max(1, min(MAX_PARALLEL_SNAPSHOTS, total))) as pool:
        list(pool.map(work, wanted))
    final = []
    for state in states:
        name = state["name"]
        if name in results:
            final.append(results[name])
        else:
            exc = errors.get(name)
            final.append({**state, "status": "failed", "error": str(exc)[-2000:] if exc else "not prepared",
                          "secret_paths": list(getattr(exc, "secret_paths", []) or [])})
    save_meta(service, job.id, build_environments=final, build_environments_source=origin)
    for state in final:
        if state["status"] == "ready":
            service.store.append_event(job.id, "build_environment_ready", payload={
                "binding": state["name"], "source": state["source"], "ref": state["ref"],
                "job_image": state["job_image"], "image_bytes": state.get("image_bytes", 0),
                "probed_env": sorted(state.get("probed_env") or {}), "skipped": len(state.get("skipped_paths") or [])})
    if errors:
        name, exc = next(iter(errors.items()))
        if isinstance(exc, EnvironmentBlocked):
            raise exc
        tail = str(exc).strip()[-1200:]
        service.store.append_event(job.id, "build_environment_failed", reason=tail, payload={"binding": name})
        raise EnvironmentBlocked(f"Build environment `{name}` could not be prepared: {tail}. Retry re-attempts the snapshot.") from exc
    return final


def request_refresh(service, job_id: str) -> None:
    """``refreshEnvironments``: drop the job's images now; the next stage (or a background prepare) rebuilds."""
    cleanup_job(job_id, service=service, reason="refresh requested", containers=False, engine=False)
    save_meta(service, job_id, environments_refresh_requested=True)


def prepare_in_background(service, job_id: str) -> threading.Thread:
    def run():
        try:
            ensure_environments(service, service.get(job_id))
        except Exception as exc:  # noqa: BLE001 - the next stage start retries and parks with the message
            try:
                service.store.append_event(job_id, "build_environment_failed", reason=str(exc)[:1000])
            except Exception:  # noqa: BLE001
                pass
    thread = threading.Thread(target=run, name=f"mucli-env-prepare-{job_id[:8]}", daemon=True)
    thread.start()
    return thread


# ------------------------------------------------------------------ cleanup + sweeper (§6)


def _ids(result) -> List[str]:
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()] if result.returncode == 0 else []


def cleanup_job(job_id: str, *, service=None, docker: Optional[Docker] = None, reason: str = "",
                images: bool = True, containers: bool = True, engine: bool = True) -> Dict[str, Any]:
    """Remove everything labelled ``io.mucli.job=<job_id>``. Best effort; failures are reported, not raised."""
    report: Dict[str, Any] = {"containers": [], "volumes": [], "networks": [], "images": [], "errors": []}
    if engine:
        try:  # host-side leftovers: the job's build output in the shared cache, the engine socket dir
            from .cache import remove_job_targets
            from .engine import remove_socket_dir
            remove_job_targets(job_id)
            remove_socket_dir(job_id)
        except Exception:  # noqa: BLE001 - housekeeping
            pass
    try:
        docker = docker or Docker()
        docker.binary
    except Exception as exc:  # noqa: BLE001 - docker unavailable; the sweeper retries
        report["errors"].append(str(exc))
        _record_cleanup(service, job_id, reason, report)
        return report
    selector = f"label={LABEL_JOB}={job_id}"
    try:
        if containers or engine:
            found = _ids(docker.run("ps", "-aq", "--filter", selector, check=False, timeout=60))
            if containers:
                found += _ids(docker.run("ps", "-aq", "--filter", f"name=^mucli-job-{job12(job_id)}-", check=False, timeout=60))
            for cid in dict.fromkeys(found):
                is_engine = docker.inspect("container", cid, '{{index .Config.Labels "io.mucli.role"}}') == "job-engine"
                if (is_engine and not engine) or (not is_engine and not containers):
                    continue
                result = docker.run("rm", "-f", cid, check=False, timeout=120)
                if result.returncode == 0:
                    report["containers"].append(cid)
                else:
                    report["errors"].append(f"container {cid}: {(result.stderr or '').strip()[:200]}")
        if engine:
            for volume in _ids(docker.run("volume", "ls", "-q", "--filter", selector, check=False, timeout=60)):
                result = docker.run("volume", "rm", "-f", volume, check=False, timeout=120)
                (report["volumes"] if result.returncode == 0 else report["errors"]).append(
                    volume if result.returncode == 0 else f"volume {volume}: {(result.stderr or '').strip()[:200]}")
            for network in _ids(docker.run("network", "ls", "-q", "--filter", selector, check=False, timeout=60)):
                result = docker.run("network", "rm", network, check=False, timeout=120)
                (report["networks"] if result.returncode == 0 else report["errors"]).append(
                    network if result.returncode == 0 else f"network {network}: {(result.stderr or '').strip()[:200]}")
        if images:
            refs = _ids(docker.run("image", "ls", "-q", "--no-trunc", "--filter", selector, check=False, timeout=60))
            if engine and service is not None:
                try:  # images the engine proxy pulled onto the host for this job
                    from .engine import pulled_images
                    refs += pulled_images(service.get(job_id))
                except KeyError:
                    pass
            # Job images (children) before their snapshots.
            refs.sort(key=lambda ref: 0 if docker.inspect("image", ref, '{{index .Config.Labels "io.mucli.role"}}') == "job-image" else 1)
            failed = remove_images(docker, refs)
            report["images"] = [r for r in dict.fromkeys(refs) if r not in failed]
            report["errors"] += [f"image {r}: still in use" for r in failed]
    except Exception as exc:  # noqa: BLE001 - docker hiccups are retried by the sweeper
        report["errors"].append(str(exc)[:300])
    if service is not None:
        try:
            job = service.get(job_id)
            states = job_states(job)
            if images and states:
                for state in states:
                    if state.get("status") in ("ready", "preparing"):
                        state["status"] = "cleaned"
                save_meta(service, job_id, build_environments=states, environments_cleaned_at=time.time())
        except KeyError:
            pass
        except Exception:  # noqa: BLE001
            pass
    _record_cleanup(service, job_id, reason, report)
    return report


def _record_cleanup(service, job_id: str, reason: str, report: Dict[str, Any]) -> None:
    if service is None or not any(report.values()):
        return
    try:
        service.store.append_event(job_id, "environment_cleanup", reason=reason or "cleanup",
                                   payload={k: (v if k == "errors" else len(v)) for k, v in report.items()})
    except Exception:  # noqa: BLE001 - deleted jobs have no event log
        pass


def cleanup_quietly(service, job_id: str, reason: str, **kwargs: Any) -> None:
    """Cleanup trigger used by commands/management: never lets docker trouble fail the command."""
    try:
        job = service.get(job_id)
        if not job_states(job) and not kwargs.pop("force", False):
            return
    except KeyError:
        pass
    try:
        cleanup_job(job_id, service=service, reason=reason, **kwargs)
    except Exception:  # noqa: BLE001 - the sweeper retries
        pass


def labelled_resources(docker: Docker) -> Dict[str, Dict[str, List[str]]]:
    """Every docker object of *this* mucli home carrying an ``io.mucli.job`` label, by job id."""
    out: Dict[str, Dict[str, List[str]]] = {}
    home = f"label={LABEL_HOME}={home_id()}"

    def add(job_id: str, kind: str, ref: str) -> None:
        if job_id:
            out.setdefault(job_id, {"containers": [], "images": [], "volumes": [], "networks": []})[kind].append(ref)

    def rows(*args: str) -> List[Tuple[str, str]]:
        result = docker.run(*args, check=False, timeout=60)
        return [(ref, job.strip()) for ref, _, job in (line.partition(" ") for line in (result.stdout or "").splitlines())]

    fmt = '{{.ID}} {{.Label "' + LABEL_JOB + '"}}'
    for ref, job_id in rows("ps", "-a", "--filter", f"label={LABEL_JOB}", "--filter", home, "--format", fmt):
        add(job_id, "containers", ref)
    for ref, job_id in rows("volume", "ls", "--filter", f"label={LABEL_JOB}", "--filter", home,
                            "--format", '{{.Name}} {{.Label "' + LABEL_JOB + '"}}'):
        add(job_id, "volumes", ref)
    for ref, job_id in rows("network", "ls", "--filter", f"label={LABEL_JOB}", "--filter", home, "--format", fmt):
        add(job_id, "networks", ref)
    image_ids = _ids(docker.run("image", "ls", "-q", "--no-trunc", "--filter", f"label={LABEL_JOB}", "--filter", home,
                                check=False, timeout=60))
    if image_ids:
        for ref, job_id in rows("image", "inspect", "--format", '{{.Id}} {{index .Config.Labels "' + LABEL_JOB + '"}}',
                                *dict.fromkeys(image_ids)):
            add(job_id, "images", ref)
    return out


def idle_since(service, job: Any) -> float:
    try:
        events = service.store.list_events_by_type(job.id, ("status_changed",))
    except Exception:  # noqa: BLE001
        events = []
    return float(events[-1].created_at) if events else float(job.updated_at or job.created_at or 0.0)


def sweep_decision(service, job: Any, core, *, archived: bool, retention_h: float, now: float) -> str:
    """Why a job's environment resources should go now ('' = keep)."""
    from ..core.states import TERMINAL, WAITING, JobState
    kept = bool((job.metadata or {}).get("environments_kept"))
    if archived:
        return "job archived"
    if core.state in TERMINAL:
        return f"job {core.state.value}"
    if core.state == JobState.PUSHED and not kept:
        return "job pushed"
    if (core.state in WAITING or core.state == JobState.FAILED) and not kept \
            and now - idle_since(service, job) > retention_h * 3600:
        return "idle retention"
    return ""


def sweep(service, *, docker: Optional[Docker] = None, now: Optional[float] = None,
          settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Controller sweeper: remove resources of gone/terminal/archived/pushed/idle jobs; stop idle engines."""
    from ..core.states import ACTIVE
    from ..job_settings import rule
    now = time.time() if now is None else now
    try:
        docker = docker or Docker()
        found = labelled_resources(docker)
    except Exception as exc:  # noqa: BLE001 - docker down; next tick retries
        return {"error": str(exc), "cleaned": []}
    try:
        retention_h = float(rule("jobs.environment_retention_h", settings))
    except (OSError, ValueError, TypeError):
        retention_h = 72.0
    try:
        from ..management import JobManagementService
        archived = JobManagementService(service).archived_ids()
    except Exception:  # noqa: BLE001
        archived = set()
    cleaned, stopped = [], []
    for job_id, resources in found.items():
        try:
            job = service.get(job_id)
        except KeyError:
            cleanup_job(job_id, docker=docker, reason="job deleted")
            cleaned.append(job_id)
            continue
        core = service.store.core_state(job_id)
        reason = sweep_decision(service, job, core, archived=job_id in archived, retention_h=retention_h, now=now)
        if reason:
            cleanup_job(job_id, service=service, docker=docker, reason=reason)
            cleaned.append(job_id)
        elif core.state not in ACTIVE and resources["containers"]:
            from .engine import engine_name
            name = engine_name(job_id)
            if docker.inspect("container", name, "{{.State.Running}}") == "true":
                docker.run("stop", "-t", "10", name, check=False, timeout=60)
                stopped.append(job_id)
    return {"cleaned": cleaned, "stopped_engines": stopped}


# ------------------------------------------------------------------ read models


def list_sources(*, docker: Optional[Docker] = None, include_images: bool = True) -> Dict[str, Any]:
    """Pickers: container-mode environments, templates, local images, Docker-in-Docker and GPU status."""
    environments, templates, images = [], [], []
    try:
        from mu.container.supervisor import ContainerSupervisor
        for ref in ContainerSupervisor().list_environments(refresh=False):
            environments.append({"name": default_name("environment", ref.name), "container": ref.name, "image": ref.image,
                                 "status": ref.status, "gpus": ref.gpu_request or "", "egress_allow": list(ref.egress_allow or []),
                                 "sessions": list(ref.attached_sessions or []), "spec": f"environment:{ref.name}"})
    except Exception:  # noqa: BLE001 - registry unreadable
        pass
    try:
        from mu.container.templates import TemplateRegistry
        gpu_of = {e["container"]: e["gpus"] for e in environments}
        for template in TemplateRegistry().list_templates():
            templates.append({"name": template.name, "image": template.image, "description": template.description,
                              "spec": f"template:{template.name}", "size": "", "size_bytes": 0,
                              "gpus": str(gpu_of.get(template.source_container) or ""),
                              "job_setup": list(template.job_setup or []),
                              "egress_allow": list(template.egress_allow or [])})
    except Exception:  # noqa: BLE001
        pass
    sysbox = False
    gpu: Dict[str, Any] = {"supported": False, "reason": "Docker is unavailable"}
    try:
        docker = docker or Docker()
        for row in templates:
            try:
                row["size_bytes"] = int(docker.inspect("image", row["image"], "{{.Size}}") or 0)
            except (ValueError, OSError, subprocess.SubprocessError):
                row["size_bytes"] = 0
            row["size"] = human_size(row["size_bytes"]) if row["size_bytes"] else ""
        if include_images:
            result = docker.run("image", "ls", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}", check=False, timeout=30)
            for line in (result.stdout or "").splitlines():
                ref, _, size = line.partition("\t")
                if "<none>" in ref or ref.startswith(("mucli/job-", "mucli-job-toolchain", "mucli/template-")):
                    continue
                images.append({"name": ref, "size": size, "spec": f"image:{ref}"})
        sysbox = sysbox_available(docker)
        from mu.container.hardware import detect_hardware
        gpu = dict(detect_hardware(docker.runner).get("gpu") or {})
    except Exception:  # noqa: BLE001
        pass
    return {"environments": environments, "templates": templates, "images": images[:300],
            "docker_in_docker": {"available": sysbox, "engine": "sysbox" if sysbox else "",
                                 "message": "" if sysbox else SYSBOX_MESSAGE, "install": SYSBOX_GUIDANCE},
            "gpu": {"supported": bool(gpu.get("supported")), "reason": str(gpu.get("reason") or ""),
                    "devices": gpu.get("devices") or []}}


def human_size(value: int) -> str:
    """``2.1 GB`` (decimal units, like ``docker image ls``)."""
    size = float(value or 0)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "kB") else f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} TB"


def _domain_covered(domain: str, allowed: Iterable[str]) -> bool:
    domain = str(domain or "").strip().lower()
    for raw in allowed or ():
        rule = str(raw or "").strip().lower().rstrip(".")
        if not rule:
            continue
        if rule == domain:
            return True
        if rule.startswith("*.") and (domain.endswith(rule[1:]) or domain == rule[2:]):
            return True
    return False


def template_fit(template_egress: Iterable[str], detected: Optional[Dict[str, Any]],
                 settings: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    """Fit of a template for a repository (§3.1): the registries the repository's lockfiles need
    that neither the template's egress nor Settings → Extra egress domains allow, and the
    detected toolchains. ``detected`` is ``detect.summary``."""
    detected = dict(detected or {})
    try:
        from ..job_settings import rule
        extra = list(rule("sandbox.egress_extra", settings) or [])
    except Exception:  # noqa: BLE001 - unreadable settings: judge on the template alone
        extra = []
    allowed = [*filter_model_hosts(template_egress or []), *extra]
    missing = [d for d in detected.get("egress") or [] if not _domain_covered(d, allowed)]
    return {"missing_egress": list(dict.fromkeys(missing)), "toolchains": list(detected.get("toolchains") or [])}


def _same_repository(a: str, b: str) -> bool:
    return bool(a and b) and os.path.realpath(os.path.expanduser(a)) == os.path.realpath(os.path.expanduser(b))


def remembered_template(repository: str, settings: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
    """``{"source": "template:<name>"}`` when Settings → Toolchains maps exactly this repository
    (whole repository, no paths) to a template; else ``None``."""
    if not str(repository or "").strip():
        return None
    from .toolchain import toolchain_rows
    try:
        rows = toolchain_rows(settings)
    except Exception:  # noqa: BLE001
        return None
    for row in rows:
        if not _same_repository(str(row.get("repository") or ""), repository):
            continue
        base = str(row.get("base_image") or "").strip()
        if base.startswith("template:") and not str(row.get("paths") or "").strip():
            return {"source": base}
    return None


def remember_template(repository: str, template: str) -> Dict[str, Any]:
    """"Remember for this workspace": write or update the repository's Settings → Toolchains
    row (repository → ``template:<name>``). Rows of other repositories are kept. For this
    repository only the whole-repository row (no ``paths``) is replaced; per-component rows
    (monorepo paths set in Settings) are kept, so remembering never discards that mapping."""
    repo = str(repository or "").strip()
    if not repo:
        raise ValueError("remember_template needs a workspace or repository")
    validate_ref("template", template)
    from ..job_settings import load_settings, rule, set_rule
    current = [dict(r) for r in (rule("sandbox.toolchains", load_settings()) or []) if isinstance(r, dict)]
    kept = [r for r in current
            if not _same_repository(str(r.get("repository") or ""), repo) or str(r.get("paths") or "").strip()]
    row = {"repository": os.path.realpath(os.path.expanduser(repo)), "base_image": f"template:{template}",
           "paths": "", "docker": "", "gpus": ""}
    set_rule("sandbox.toolchains", [*kept, row])
    return row


def normalize_template_choice(execution: Dict[str, Any], repository: str = "") -> Dict[str, Any]:
    """``execution.template`` → ``execution.environments = ["template:<name>"]`` (§3.1).

    ``""``/absent changes nothing. An unknown template raises ``ValueError("Unknown template
    '<name>'")``. ``remember_template: true`` writes the repository's Toolchains row and is
    dropped from the stored execution."""
    out = dict(execution or {})
    name = str(out.pop("template", "") or "").strip()
    remember = _bool(out.pop("remember_template", False))
    if not name:
        return out
    from mu.container.templates import TemplateRegistry
    if not REF_RE.fullmatch(name) or TemplateRegistry().get(name) is None:
        raise ValueError(f"Unknown template '{name}'")
    out["environments"] = [f"template:{name}"]
    if remember and str(repository or "").strip():
        remember_template(repository, name)
    return out


def describe(job: Any) -> Dict[str, Any]:
    """Job VM ``build_environments`` part (metadata only; no docker calls)."""
    from .job_setup import describe_setup
    meta = dict(getattr(job, "metadata", None) or {})
    states = job_states(job)
    rows = []
    for state in states:
        rows.append({"name": state.get("name"), "source": state.get("source"), "ref": state.get("ref"),
                     "paths": list(state.get("paths") or []), "primary": bool(state.get("primary")),
                     "docker": bool(state.get("docker")), "gpus": state.get("gpus") or "",
                     "status": state.get("status") or "", "error": state.get("error") or "",
                     "image_bytes": int(state.get("image_bytes") or 0), "home_bytes": int(state.get("home_bytes") or 0),
                     "prepared_at": state.get("prepared_at"), "probed_env": sorted(state.get("probed_env") or {}),
                     "skipped_paths": list(state.get("skipped_paths") or [])[:100],
                     "secret_paths": list(state.get("secret_paths") or [])[:50],
                     "engine": "sysbox" if state.get("docker") else "", "job_image": state.get("job_image") or "",
                     "label": binding_label(state), "job_setup": list(state.get("job_setup") or []),
                     "setup": describe_setup(job, str(state.get("name") or ""))})
    return {"source": str(meta.get("build_environments_source") or ("default" if not states else "")),
            "kept": bool(meta.get("environments_kept")), "refresh_pending": bool(meta.get("environments_refresh_requested")),
            "bindings": rows, "count": len(rows), "bytes": sum(r["image_bytes"] for r in rows if r["status"] == "ready"),
            "summary": " · ".join(r["label"] for r in rows)}


def binding_label(state: Dict[str, Any]) -> str:
    """``rust-build → resq-backend, stream-processor-rs`` · ``compose-it ⬢ docker``."""
    text = str(state.get("name") or "")
    paths = list(state.get("paths") or [])
    if paths:
        text += " → " + ", ".join(paths)
    if state.get("docker"):
        text += " ⬢ docker"
    if state.get("gpus"):
        text += f" · gpus {state['gpus']}"
    return text


def preview(job: Any, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """New job sheet: the environment set a job would resolve to (no snapshots)."""
    extra = _preview_extras(job)
    try:
        bindings, origin = resolve_bindings(job, settings)
        rows = [b.to_dict() for b in bindings]
        out = {"source": origin, "bindings": [{**r, "label": binding_label(r)} for r in rows],
               "summary": " · ".join(binding_label(r) for r in rows), "error": "", **extra}
        from .devcontainer import is_approved
        from .toolchain import job_repository
        pending = [r for r in rows if r.get("approval") and not is_approved(job_repository(job), r["approval"])]
        if pending:
            unique = {r["approval"]: r for r in reversed(pending)}
            out["approval_required"] = [{"binding": r["name"], "ref": r["ref"], "fingerprint": r["approval"]}
                                        for r in pending if unique.get(r["approval"]) is r]
        egress = _egress_offer(job)
        if egress:
            out.setdefault("approval_required", []).append(egress)
        if origin == "devcontainer" or any(r.get("approval") for r in rows):
            out.pop("suggestion", None)
        return out
    except ValueError as exc:
        return {"source": "error", "bindings": [], "summary": "", "error": str(exc), **extra}


def _egress_offer(job: Any) -> Optional[Dict[str, Any]]:
    """The repository's unapproved ``[sandbox] egress`` as an ``approval_required`` row (it is
    not applied until approved; approving goes through the same approvals store)."""
    try:
        from .repo_config import egress_approval
        request = egress_approval(job)
    except Exception:  # noqa: BLE001 - advisory
        return None
    if not request or request["approved"]:
        return None
    return {"binding": "egress", "ref": request["ref"], "fingerprint": request["fingerprint"], "kind": "egress",
            "label": request["label"]}


def _preview_extras(job: Any) -> Dict[str, Any]:
    """Detected toolchains (manifests at the base commit) and an unapproved devcontainer suggestion."""
    out: Dict[str, Any] = {}
    try:
        from .detect import detect_for_job, summary
        found = detect_for_job(job)
        if found:
            out["detected"] = summary(found)
    except Exception:  # noqa: BLE001 - detection is advisory
        pass
    try:
        from .devcontainer import approval_message, approval_request, repository_environment
        env = repository_environment(job, implicit_devcontainer=True)
        if env and not env.get("error") and env.get("origin") == "devcontainer" and not env.get("approved"):
            out["suggestion"] = {"message": approval_message(env), "approval": approval_request(job, env)}
    except Exception:  # noqa: BLE001
        pass
    return out


def jobs_using(service, environment: str) -> List[str]:
    """Non-terminal jobs whose build environments snapshot ``environment`` (Environments page badge)."""
    from ..core.states import TERMINAL
    name = str(environment or "")
    names = {name, name[len("mucli-"):] if name.startswith("mucli-") else f"mucli-{name}"}
    out = []
    for job in service.list_unarchived(limit=1000):
        states = job_states(job)
        if not any(s.get("source") == "environment" and s.get("ref") in names for s in states):
            continue
        try:
            if service.store.core_state(job.id).state in TERMINAL:
                continue
        except Exception:  # noqa: BLE001
            continue
        out.append(job.id)
    return out


def jobs_using_template(service, template: str) -> List[str]:
    """Non-terminal jobs bound to ``template:<name>`` (template card "Used by N jobs")."""
    from ..core.states import TERMINAL
    name = str(template or "")
    out = []
    for job in service.list_unarchived(limit=1000):
        bound = any(s.get("source") == "template" and s.get("ref") == name for s in job_states(job))
        if not bound:
            try:
                bound = any(b.source == "template" and b.ref == name
                            for b in bindings_from_list((job.execution or {}).get("environments") or []))
            except ValueError:
                bound = False
        if not bound:
            continue
        try:
            if service.store.core_state(job.id).state in TERMINAL:
                continue
        except Exception:  # noqa: BLE001
            continue
        out.append(job.id)
    return out


def scan_blocked(service, environment: str) -> List[Dict[str, Any]]:
    """Jobs whose secret scan blocked this environment."""
    name = str(environment or "")
    names = {name, name[len("mucli-"):] if name.startswith("mucli-") else f"mucli-{name}"}
    out = []
    for job in service.list_unarchived(limit=1000):
        for state in job_states(job):
            if state.get("source") == "environment" and state.get("ref") in names and state.get("secret_paths"):
                out.append({"job_id": job.id, "binding": state.get("name"), "paths": list(state["secret_paths"])[:20]})
    return out


def kept_jobs(service) -> List[Dict[str, Any]]:
    """Settings → Build environments: every kept job and the disk its images use."""
    out = []
    for job in service.list(limit=2000):
        meta = dict(job.metadata or {})
        if not meta.get("environments_kept"):
            continue
        info = describe(job)
        out.append({"id": job.id, "title": job.title, "bytes": info["bytes"], "bindings": info["summary"]})
    return out
