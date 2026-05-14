"""Pre-tool guard for bash invocations that target secret material.

Two checks run before `bash` / `bash_background` reach the shell:

1. **Argument-path scan** — every path-shaped token in the command is
   passed through `core.secret_paths.is_denied_path`. Catches direct
   exfiltration like `cat ~/.ssh/id_rsa`, `cp ~/.aws/credentials /tmp/x`,
   or `tar czf - ~/.ssh`.

2. **Risky-command pattern match** — known broad-leakage commands like
   bare `env` / `printenv`, history dumps, or `find / -name id_rsa` are
   blocked even when no explicit path argument is present.

The session variable `security_allow_secret_paths` (truthy) bypasses both
checks. Set with `/set security_allow_secret_paths true` only for
deliberate one-off work; it does not survive a session reload.

The output scrubber in `core.secret_paths.redact_secrets` continues to
apply even when this guard is bypassed.
"""

from __future__ import annotations

import re
from typing import Optional

from core.secret_paths import (
    extract_paths_from_command,
    is_denied_path,
)

from .hooks import (
    HookContext,
    HookRegistry,
    HookResult,
    HookSpec,
    default_registry,
)


# Tools whose first/only argument is a shell command we want to inspect.
GUARDED_TOOLS = {"bash", "bash_background"}


# Patterns matched against the raw command string. Each entry: (label, regex).
# Patterns are intentionally narrow — we want to block obvious exfiltration,
# not interfere with normal workflows.
_RISKY_COMMAND_PATTERNS = [
    # Bare `env` or `printenv` (no args) dumps every env var, which on dev
    # machines usually means API keys.
    ("env-dump", re.compile(r"(?:^|[\s|;&])(?:env|printenv)\s*(?:$|[|;&])")),
    # Shell history files
    ("history-dump", re.compile(r"\b(?:cat|less|more|head|tail|grep)\b[^\n|;&]*\.(?:bash|zsh|sh|fish)_history\b")),
    # Recursive search for known key filenames anywhere on disk
    (
        "key-hunt",
        re.compile(
            r"\bfind\b[^\n|;&]*-name\s+['\"]?(?:id_rsa|id_ed25519|id_ecdsa|id_dsa|\*\.pem|\*\.key)['\"]?",
        ),
    ),
    # Reading from /proc/*/environ to grab a running process's env
    ("proc-environ", re.compile(r"/proc/\d+/environ\b")),
    # Common base64-pipe pattern for secret stealing
    (
        "key-pipe-base64",
        re.compile(
            r"(?:id_rsa|id_ed25519|\.aws/credentials|\.ssh/[A-Za-z0-9_\-]+)\b[^\n]*\|\s*base64",
        ),
    ),
]


def _override_active(ctx: HookContext) -> bool:
    vars_ = (ctx.variables or {})
    if ctx.session is not None:
        session_vars = getattr(ctx.session, "variables", None) or {}
        vars_ = {**session_vars, **vars_}
    val = vars_.get("security_allow_secret_paths")
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(val)


def _build_envelope(tool_name: str, reason: str, command: str) -> dict:
    return {
        "ok": False,
        "error_code": "secret_guard_blocked",
        "message": (
            f"Secret-guard blocked '{tool_name}': {reason}. "
            "If this is intentional, the user can set "
            "`security_allow_secret_paths` to true for this session "
            "(/set security_allow_secret_paths true). Until then, this "
            "command will not run."
        ),
        "data": {
            "tool_name": tool_name,
            "reason": reason,
            "command_preview": command[:200],
        },
        "artifacts": [],
        "telemetry": {
            "tool_name": tool_name,
            "execution_source": "secret_guard_block",
            "reason": reason,
        },
    }


def _check_command(command: str) -> Optional[str]:
    """Return a refusal reason if the command should be blocked, else None."""
    if not command:
        return None

    # 1. Path-argument scan
    for token in extract_paths_from_command(command):
        denied, why = is_denied_path(token)
        if denied:
            return f"command references {why}: {token!r}"

    # 2. Risky-command patterns
    for label, pattern in _RISKY_COMMAND_PATTERNS:
        if pattern.search(command):
            return f"matched risky pattern '{label}'"

    return None


def _guard(ctx: HookContext) -> Optional[HookResult]:
    tool_name = ctx.tool_name or ""
    if tool_name not in GUARDED_TOOLS:
        return None
    if _override_active(ctx):
        return None

    args = ctx.tool_args or {}
    command = str(args.get("command") or "")
    reason = _check_command(command)
    if reason is None:
        return None

    return HookResult(
        action="short_circuit",
        payload=_build_envelope(tool_name, reason, command),
        data={"reason": "secret_guard"},
    )


def install(registry: Optional[HookRegistry] = None) -> None:
    """Register the bash secret-guard hook. Idempotent."""
    reg = registry or default_registry
    reg.remove("secret_guard_bash")
    reg.add(
        HookSpec(
            name="secret_guard_bash",
            point="pre_tool",
            # Run before plan_mode (priority 10) so a deliberately-secret
            # bash command is refused with the secret reason, not the
            # plan-mode reason.
            priority=5,
            handler=_guard,
        )
    )


install()


__all__ = ["install", "GUARDED_TOOLS"]
