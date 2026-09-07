"""Unattended shell commands must fail prompts fast and clean up descendants."""

from __future__ import annotations

import shlex
import time
from pathlib import Path

import pytest

from mu.tools.shell.background import BackgroundTaskRegistry
from mu.tools.shell.handlers import bash_command
from mu.tools.shell.process import noninteractive_environment
from mu.tools.shell import process as shell_process


class _FolderContext:
    folders = ["/tmp"]

    @staticmethod
    def is_ignored(_path):
        return False


def _child_is_running(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[2]
    except (FileNotFoundError, OSError, IndexError):
        return False
    return state != "Z"


def test_noninteractive_environment_is_fail_fast_but_preserves_overrides():
    environment = noninteractive_environment(
        {"PATH": "/bin", "GIT_TERMINAL_PROMPT": "1"}
    )

    assert environment["PATH"] == "/bin"
    assert environment["GIT_TERMINAL_PROMPT"] == "1"
    assert environment["GIT_PAGER"] == "cat"
    assert environment["PIP_NO_INPUT"] == "1"
    assert environment["SSH_ASKPASS_REQUIRE"] == "never"


def test_bash_has_closed_stdin_and_no_controlling_terminal():
    output = bash_command(
        "if read -r value; then echo unexpected-input; else echo stdin-closed; fi; "
        "(exec </dev/tty) 2>/dev/null && echo tty-open || echo no-controlling-tty; "
        "printf 'git-prompt=%s pager=%s\\n' "
        '"$GIT_TERMINAL_PROMPT" "$GIT_PAGER"',
        _FolderContext(),
        timeout_seconds=5,
    )

    assert "stdin-closed" in output
    assert "no-controlling-tty" in output
    assert "git-prompt=0 pager=cat" in output
    assert "Exit code: 0" in output


def test_prompt_attempt_fails_immediately_with_recovery_hint():
    started = time.monotonic()
    output = bash_command(
        "python3 -c 'input(\"Password: \")'",
        _FolderContext(),
        timeout_seconds=5,
    )

    assert time.monotonic() - started < 2.0
    assert "EOFError" in output
    assert "Interactive input is disabled" in output
    assert "StrictHostKeyChecking=accept-new" in output


def test_timeout_terminates_background_descendant(tmp_path):
    pid_file = tmp_path / "child.pid"
    output = bash_command(
        f"sleep 30 & echo $! > {shlex.quote(str(pid_file))}; wait",
        _FolderContext(),
        timeout_seconds=1,
    )

    assert "timed out after 1 seconds" in output
    assert "entire process group were terminated" in output
    assert "do not clear the prompt-control environment" in output
    child_pid = int(pid_file.read_text(encoding="utf-8").strip())
    deadline = time.monotonic() + 1.0
    while _child_is_running(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _child_is_running(child_pid)


def test_caller_interrupt_terminates_detached_process_group(monkeypatch):
    class _InterruptedProcess:
        pid = 12345
        returncode = None

        def __init__(self):
            self.communicate_calls = 0

        def communicate(self, timeout=None):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise KeyboardInterrupt
            return "", ""

    process = _InterruptedProcess()
    terminated = []
    monkeypatch.setattr(shell_process.subprocess, "Popen", lambda *_a, **_kw: process)
    monkeypatch.setattr(
        shell_process,
        "_terminate_process_group",
        lambda candidate: terminated.append(candidate),
    )

    with pytest.raises(KeyboardInterrupt):
        shell_process.run_noninteractive_shell("sleep 30", cwd="/tmp", timeout=5)

    assert terminated == [process]
    assert process.communicate_calls == 2


def test_background_bash_cannot_wait_for_input():
    registry = BackgroundTaskRegistry()
    task = registry.start("python3 -c 'input(\"Password: \")'")
    deadline = time.monotonic() + 2.0
    while task.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)

    assert task.process.poll() is not None
    assert task.process.returncode != 0
    registry.shutdown()
