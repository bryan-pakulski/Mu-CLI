"""Non-interactive shell process primitives.

Agent shell calls have no mechanism for answering a child process prompt.  A
child that inherits MuCLI's terminal can nevertheless open ``/dev/tty`` and
wait forever (OpenSSH host-key and password prompts are common examples).
Run every unattended shell in its own session with closed stdin, and terminate
the entire process group when its wall-clock budget expires.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping

_NONINTERACTIVE_DEFAULTS = {
    "APT_LISTCHANGES_FRONTEND": "none",
    "DEBIAN_FRONTEND": "noninteractive",
    "GCM_INTERACTIVE": "never",
    "GIT_EDITOR": "true",
    "GIT_MERGE_AUTOEDIT": "no",
    "GIT_PAGER": "cat",
    "GIT_SEQUENCE_EDITOR": "true",
    "GIT_TERMINAL_PROMPT": "0",
    "MANPAGER": "cat",
    "PAGER": "cat",
    "PIP_NO_INPUT": "1",
    "SSH_ASKPASS_REQUIRE": "never",
    "SYSTEMD_PAGER": "cat",
}


def noninteractive_environment(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an inherited environment with fail-fast unattended defaults.

    Defaults never overwrite caller-provided values. A command can therefore
    opt into a deliberate mechanism such as ``SSH_ASKPASS_REQUIRE=force`` or
    select a real editor inline when that is genuinely required.
    """

    environment = dict(os.environ if base is None else base)
    for name, value in _NONINTERACTIVE_DEFAULTS.items():
        environment.setdefault(name, value)
    return environment


def _terminate_process_group(
    process: subprocess.Popen,
    grace_seconds: float = 0.25,
) -> None:
    """Best-effort TERM/KILL of the shell and every descendant in its group."""

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    else:
        try:
            process.terminate()
        except (OSError, ProcessLookupError):
            pass

    try:
        process.wait(timeout=max(0.0, grace_seconds))
    except subprocess.TimeoutExpired:
        pass

    # The group leader may already have exited while a descendant still owns
    # stdout/stderr. Signal the known PGID even when process.poll() is set.
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    elif process.poll() is None:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass


def run_noninteractive_shell(
    command: str,
    *,
    cwd: str,
    timeout: float,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``command`` without an input terminal and with tree-safe timeout.

    ``start_new_session`` prevents programs such as OpenSSH from falling back
    to MuCLI's controlling terminal. ``DEVNULL`` gives ordinary stdin readers
    immediate EOF. On timeout the complete process group is stopped before the
    partial output is returned in ``TimeoutExpired``.
    """

    argv = ["/bin/bash", "-lc", command]
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
        env=noninteractive_environment(environment),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            argv,
            timeout,
            output=stdout,
            stderr=stderr,
        ) from None
    except BaseException:
        # The child is deliberately outside MuCLI's terminal process group,
        # so Ctrl+C/cancellation will not reach it implicitly. Never leave the
        # detached command behind when the caller is interrupted.
        _terminate_process_group(process)
        process.communicate()
        raise
    return subprocess.CompletedProcess(
        args=argv,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


__all__ = ["noninteractive_environment", "run_noninteractive_shell"]
