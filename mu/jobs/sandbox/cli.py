"""The one place the job sandbox decides which container CLI it shells out to.

``jobs.container_cli`` = ``docker`` | ``podman`` | ``auto`` (default: Docker if installed, else
Podman). Every sandbox module asks :func:`container_cli` for the executable name instead of
hard-coding ``docker``; Podman's CLI accepts the same stage-container options.

Also here: host facts that change how bind mounts and users are rendered
(SELinux relabelling, rootless Podman's ``--userns=keep-id``) and the shared settings
reader for sandbox keys that may not have a schema entry yet.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

CLIS = ("docker", "podman")
# Never relabelled (Docker refuses, and a system tree must keep its own SELinux type).
_NO_RELABEL = ("/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/proc", "/sys", "/dev", "/var/lib")
SELINUX_ENFORCE = "/sys/fs/selinux/enforce"


def setting(path: str, default: Any, settings: Optional[Dict[str, Any]] = None) -> Any:
    """Effective job-settings value for ``path`` that never raises (absent → ``default``)."""
    try:
        from ..job_settings import rule
        value = rule(path, settings)
    except (KeyError, OSError, ValueError, TypeError):
        value = None
    return default if value is None or value == "" else value


def container_cli(settings: Optional[Dict[str, Any]] = None) -> str:
    """``docker`` or ``podman`` (an executable *name*; callers resolve it on PATH)."""
    wanted = str(setting("jobs.container_cli", "auto", settings) or "auto").strip().lower()
    if wanted in CLIS:
        return wanted
    for name in CLIS:
        if shutil.which(name):
            return name
    return "docker"


def is_podman(settings: Optional[Dict[str, Any]] = None, *, run=subprocess.run) -> bool:
    """True when the selected CLI is Podman (also Podman's ``docker`` shim)."""
    name = container_cli(settings)
    if name == "podman":
        return True
    binary = shutil.which(name)
    if not binary:
        return False
    if binary in _SHIM and run is subprocess.run:
        return _SHIM[binary]
    try:
        out = run([binary, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    found = "podman" in (out.stdout or "").lower()
    if run is subprocess.run:
        _SHIM[binary] = found
    return found


_SHIM: Dict[str, bool] = {}


def selinux_enforcing(path: str = SELINUX_ENFORCE) -> bool:
    try:
        with open(path, encoding="ascii") as handle:
            return handle.read().strip() == "1"
    except OSError:
        return False


def _relabel_allowed(src: str) -> bool:
    return src != "/" and not any(src == p or src.startswith(p + "/") for p in _NO_RELABEL)


def bind_mount(src: str, dst: str, readonly: bool, *, podman: bool = False, selinux: bool = False) -> List[str]:
    """Arguments for one bind mount.

    Plain hosts: ``--mount type=bind,...``. SELinux enforcing: the source gets the shared
    container label (``z`` / ``relabel=shared``) so a confined container may read it; shared,
    not private, because a job's stage, check and engine containers use the same worktree and
    cache concurrently and a private label would revoke the others' access. System trees are
    never relabelled. Docker's ``--mount`` has no relabel option, so Docker uses ``-v``."""
    if "," in src or "," in dst:
        raise ValueError("commas in sandbox mount paths are unsupported")
    relabel = selinux and _relabel_allowed(src)
    if relabel and not podman:
        if ":" in src or ":" in dst:
            raise ValueError("colons in sandbox mount paths are unsupported on SELinux hosts")
        return ["-v", f"{src}:{dst}:{'ro,' if readonly else ''}z"]
    spec = f"type=bind,src={src},dst={dst}" + (",readonly" if readonly else "")
    if relabel:
        spec += ",relabel=shared"
    return ["--mount", spec]


def user_args(*, podman: bool = False) -> List[str]:
    """Stages run as the host user. Rootless Podman needs ``keep-id`` so that uid maps to the host
    user (otherwise it is a subordinate uid that cannot write the bind-mounted worktree)."""
    args = ["--user", f"{os.getuid()}:{os.getgid()}"]
    if podman and os.getuid() != 0:
        args = ["--userns", "keep-id", *args]
    return args


def info(fmt: str, *, settings: Optional[Dict[str, Any]] = None, run=subprocess.run, timeout: float = 20) -> Optional[str]:
    """``<cli> info --format <fmt>`` (None when the CLI or daemon is unavailable)."""
    binary = shutil.which(container_cli(settings))
    if not binary:
        return None
    try:
        out = run([binary, "info", "--format", fmt], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout or "").strip() if out.returncode == 0 else None
