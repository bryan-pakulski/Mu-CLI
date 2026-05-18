"""Pin the background-bash primitive — fire-and-poll task registry.

The agent needs to run long commands (test watchers, dev servers,
builds) without blocking the synchronous tool loop. The
`BackgroundTaskRegistry` exposes them as `bash_background`,
`bash_status`, `bash_logs`, `bash_kill`, `bash_list` tools.
"""

import json
import time

import pytest

from core.background_tasks import BackgroundTaskRegistry, summarize_task
import mu.tools as _mu_tools


def _wait_until_done(task, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.process.poll() is not None:
            # Give pump threads + reaper one more tick.
            time.sleep(0.1)
            return
        time.sleep(0.05)
    pytest.fail(f"task {task.task_id} did not finish within {timeout}s")


def test_start_runs_command_and_captures_stdout():
    registry = BackgroundTaskRegistry()
    task = registry.start("echo hello-world && echo second-line")
    _wait_until_done(task)
    assert task.status() == "completed"
    out = "\n".join(task.stdout_buf)
    assert "hello-world" in out
    assert "second-line" in out


def test_failed_command_status_is_failed():
    registry = BackgroundTaskRegistry()
    task = registry.start("false")
    _wait_until_done(task)
    assert task.status() == "failed"
    assert task.exit_code != 0


def test_kill_terminates_running_task():
    registry = BackgroundTaskRegistry()
    task = registry.start("sleep 30")
    # Give the OS a moment to actually schedule the process.
    time.sleep(0.1)
    registry.kill(task.task_id, grace_seconds=1.0)
    _wait_until_done(task, timeout=3.0)
    assert task.status() == "killed"
    assert task.killed is True


def test_list_returns_every_started_task():
    registry = BackgroundTaskRegistry()
    a = registry.start("echo a")
    b = registry.start("echo b")
    _wait_until_done(a)
    _wait_until_done(b)
    ids = {t.task_id for t in registry.list()}
    assert {a.task_id, b.task_id} == ids


def test_shutdown_kills_running_tasks():
    registry = BackgroundTaskRegistry()
    task = registry.start("sleep 30")
    time.sleep(0.1)
    registry.shutdown()
    _wait_until_done(task, timeout=3.0)
    assert task.status() in {"killed", "failed"}


def test_unknown_cwd_raises():
    registry = BackgroundTaskRegistry()
    with pytest.raises(ValueError):
        registry.start("echo x", cwd="/no/such/dir/exists")


def test_empty_command_raises():
    registry = BackgroundTaskRegistry()
    with pytest.raises(ValueError):
        registry.start("   ")


def test_summarize_task_carries_metadata():
    registry = BackgroundTaskRegistry()
    task = registry.start("echo summary-marker", name="my-job")
    _wait_until_done(task)
    summary = summarize_task(task, tail_lines=10)
    assert summary["task_id"] == task.task_id
    assert summary["name"] == "my-job"
    assert summary["status"] == "completed"
    assert summary["exit_code"] == 0
    assert any("summary-marker" in line for line in summary["stdout_tail"])


# ---------------------------------------------------------------- tool handlers


def _ctx_with(registry):
    """Build a `ToolExecutionContext` whose session exposes the given
    BackgroundTaskRegistry — the bg `@tool` handlers read it as
    `context.session.background_tasks`."""
    session = type("S", (), {"background_tasks": registry})()
    return _mu_tools.build_tool_context(
        folder_context=None, ui=None, variables={}, session=session
    )


def _run(name, args, registry):
    envelope = _mu_tools.execute(name, args, _ctx_with(registry))
    assert envelope["ok"] is True or envelope["ok"] is False  # well-formed
    return json.loads(envelope["message"])


def test_bash_background_handler_returns_task_id():
    registry = BackgroundTaskRegistry()
    result = _run("bash_background", {"command": "echo hi"}, registry)
    assert "task_id" in result
    assert result["status"] in {"running", "completed"}


def test_bash_status_handler_for_missing_task_reports_error():
    """A missing task surfaces as a failure envelope. The legacy handler
    returned `{"error": ...}` JSON; the new dispatcher unwraps that into
    the standard envelope shape (`ok=False`, message + data.error)."""
    registry = BackgroundTaskRegistry()
    envelope = _mu_tools.execute(
        "bash_status", {"task_id": "bg-missing"}, _ctx_with(registry)
    )
    assert envelope["ok"] is False
    assert "no such task" in envelope["message"]
    assert envelope["data"].get("error", "").startswith("no such task")


def test_bash_logs_filters_by_stream():
    registry = BackgroundTaskRegistry()
    task = registry.start("echo to-stdout && echo to-stderr >&2")
    _wait_until_done(task)
    only_stderr = _run(
        "bash_logs", {"task_id": task.task_id, "stream": "stderr"}, registry
    )
    assert "stderr" in only_stderr
    assert "stdout" not in only_stderr
    assert any("to-stderr" in line for line in only_stderr["stderr"])


def test_bash_list_handler_counts_tasks():
    registry = BackgroundTaskRegistry()
    registry.start("echo one")
    registry.start("echo two")
    result = _run("bash_list", {}, registry)
    assert result["count"] == 2


def test_bash_kill_handler_marks_killed():
    registry = BackgroundTaskRegistry()
    task = registry.start("sleep 30")
    time.sleep(0.1)
    result = _run("bash_kill", {"task_id": task.task_id}, registry)
    assert result["status"] in {"killed", "failed"}
    assert result["killed"] is True


def test_plan_mode_blocks_bash_background():
    """When plan_mode is on, the `bash_background` tool must be on the
    WRITE_TOOLS blocklist so the pre_tool hook denies it."""
    from mu.agent.plan_mode import WRITE_TOOLS

    assert "bash_background" in WRITE_TOOLS
    assert "bash_kill" in WRITE_TOOLS
    # Reads remain allowed.
    assert "bash_status" not in WRITE_TOOLS
    assert "bash_logs" not in WRITE_TOOLS
    assert "bash_list" not in WRITE_TOOLS


def test_tool_definitions_registered():
    """Every bash_* tool must show up in TOOLS and have a handler."""
    from core.tools import TOOLS, TOOL_HANDLERS

    names = {t.name for t in TOOLS}
    for n in (
        "bash_background",
        "bash_status",
        "bash_logs",
        "bash_kill",
        "bash_list",
    ):
        assert n in names, f"{n} missing from TOOLS"
        assert n in TOOL_HANDLERS, f"{n} missing from TOOL_HANDLERS"
