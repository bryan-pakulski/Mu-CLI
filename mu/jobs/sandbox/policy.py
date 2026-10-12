"""Host-owned stage policy; never accept Docker options from an agent."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

IMAGE = "mucli-job-runtime:latest"
STAGES = ("plan", "implement", "review", "checks")


@dataclass(frozen=True)
class SandboxPolicy:
    image: str = IMAGE
    cpu: float = 4
    memory: str = "8g"
    timeout: int = 900
    egress_extra: tuple[str, ...] = ()
    max_file_bytes: int = 20 * 1024 * 1024
    max_tokens: int = 2_000_000
    max_cost_usd: float = 50.0

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
                stage, {"implement": 3600, "checks": 1800}.get(stage, 900))))),
            egress_extra=tuple(extra) if stage in ("implement", "checks") else (),
            max_file_bytes=max(1, int(raw.get("max_file_bytes", 20 * 1024 * 1024))),
            max_tokens=max(1, int(raw.get("max_tokens", 2_000_000))),
            max_cost_usd=max(0, float(raw.get("max_cost_usd", 50))),
        )


def container_args(name: str, stage: str, workspace: str, inputs: str, output: str,
                   relay: str, policy: SandboxPolicy, token: str) -> list[str]:
    """Network-none + a scoped Unix socket avoids exposing any host TCP service.

    Only the disposable output directory is writable apart from implement's
    worktree. The gitdir and credentials never cross the container boundary.
    """
    if stage not in STAGES:
        raise ValueError("unknown stage")
    args = ["create", "--name", name, "--pull", "never", "--network", "none",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--read-only", "--pids-limit", "256", "--cpus", str(policy.cpu),
            "--memory", policy.memory, "--user", f"{os.getuid()}:{os.getgid()}",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
            "--workdir", "/workspace", "--label", "io.mucli.role=job-stage"]
    for src, dst, readonly in ((workspace, "/workspace", stage not in ("implement", "checks")),
                                (inputs, "/artifacts", True), (output, "/output", False),
                                (relay, "/relay", True)):
        src = str(Path(src).resolve(strict=True))
        if "," in src:
            raise ValueError("commas in sandbox mount paths are unsupported")
        args += ["--mount", f"type=bind,src={src},dst={dst}" + (",readonly" if readonly else "")]
    # The worktree's .git pointer must not be replaced by the agent. The actual
    # git directory it references is outside /workspace and is never mounted.
    pointer = Path(workspace) / ".git"
    if pointer.exists():
        if not pointer.is_file() or pointer.is_symlink():
            raise ValueError("job sandbox requires a managed git worktree")
        args += ["--mount", f"type=bind,src={pointer.resolve()},dst=/workspace/.git,readonly"]
    from mu.security.secret_paths import is_denied_path
    for directory, dirs, files in os.walk(workspace, followlinks=False):
        dirs[:] = [d for d in dirs if d != ".git"]
        for filename in [*dirs, *files]:
            path = Path(directory) / filename
            if path.is_symlink() and not path.resolve().is_relative_to(Path(workspace).resolve()):
                raise ValueError(f"workspace symlink escapes the sandbox: {path.relative_to(workspace)}")
            if is_denied_path(str(path))[0]:
                # Fail closed: an arbitrary shell could bypass a tool-level
                # denylist if secret files were mounted with the workspace.
                raise ValueError(f"secret path must be removed from the job worktree before sandboxing: {path.relative_to(workspace)}")
    for key, value in {"HOME": "/tmp", "MUCLI_HOME": "/tmp/mucli",
                       "MUCLI_MODEL_RELAY": "/relay/model.sock", "MUCLI_STAGE_TOKEN": token,
                       "MUCLI_JOB_STAGE": stage, "PYTHONDONTWRITEBYTECODE": "1"}.items():
        args += ["-e", f"{key}={value}"]
    return args + ["--entrypoint", "python3", policy.image, "-m", "mu.jobs.sandbox.worker"]


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
