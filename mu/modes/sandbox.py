"""Container runner for checks that must not touch the host (modes v2 §4.5 S2).

Security PoCs (and other untrusted probes) run in the jobs runtime image
(``mucli-job-runtime``) with the network off, every capability dropped, no new
privileges, CPU / memory / process limits, as the host user, against a
throwaway copy of the workspace. Secret files (``.env``, keys — the same
rules as the secret-path guard) are left out of the copy, and the copy is
deleted afterwards: a PoC cannot write outside it or reach the network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Any, Tuple

IMAGE = "mucli-job-runtime:latest"
_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
_AVAILABLE: dict = {}


def _cli() -> str:
    try:
        from mu.jobs.sandbox.cli import container_cli

        return container_cli()
    except Exception:  # noqa: BLE001
        return "docker"


def docker_available(max_age_s: float = 60.0) -> bool:
    """True when the container CLI reaches a daemon and the runtime image exists."""
    cached = _AVAILABLE.get("ok")
    if cached is not None and time.monotonic() - cached[1] < max_age_s:
        return cached[0]
    ok = False
    try:
        result = subprocess.run([_cli(), "image", "inspect", "--format", "{{.Id}}", IMAGE],
                                capture_output=True, text=True, timeout=20)
        ok = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    _AVAILABLE["ok"] = (ok, time.monotonic())
    return ok


def copy_workspace(source: str, target: str) -> None:
    """Copy ``source`` to ``target`` without VCS metadata or secret files."""
    from mu.security.secret_paths import is_denied_path

    def ignore(directory: str, names: list) -> list:
        skipped = []
        for name in names:
            if name in _SKIP_DIRS:
                skipped.append(name)
                continue
            denied, _ = is_denied_path(os.path.join(directory, name))
            if denied:
                skipped.append(name)
        return skipped

    shutil.copytree(source, target, symlinks=True, ignore=ignore, dirs_exist_ok=True)


def run_in_docker(spec: Any, cwd: str, timeout: int) -> Tuple[int | None, str, bool]:
    """Run ``spec.command`` in a sandbox container over a copy of ``cwd``."""
    if not docker_available():
        raise RuntimeError(
            f"the container runner needs Docker and the {IMAGE} image (build it with "
            "`mucli jobs setup`)")
    cli = _cli()
    name = f"mucli-check-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="mucli-check-") as tmp:
        work = os.path.join(tmp, "workspace")
        copy_workspace(cwd, work)
        os.chmod(work, 0o777)
        command = [
            cli, "run", "--rm", "--name", name,
            "--network", "bridge" if getattr(spec, "network", False) else "none",
            "--cpus", "2", "--memory", "2g", "--pids-limit", "512",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp", "-e", "MUCLI_CHECK=1",
            "-v", f"{work}:/workspace", "-w", "/workspace",
            "--entrypoint", "timeout", IMAGE, "-k", "5", str(int(timeout)),
            "bash", "-c", str(spec.command),
        ]
        timed_out = False
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=timeout + 15)
            out = result.stdout or b""
            code: int | None = result.returncode
            if code == 124:  # coreutils timeout fired inside the container
                timed_out, code = True, None
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            subprocess.run([cli, "kill", name], capture_output=True, timeout=30)
            out = exc.stdout or b""
            code = None
    return code, out.decode("utf-8", errors="replace"), timed_out


__all__ = ["IMAGE", "copy_workspace", "docker_available", "run_in_docker"]
