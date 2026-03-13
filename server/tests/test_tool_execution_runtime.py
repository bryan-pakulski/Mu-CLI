import asyncio
from types import SimpleNamespace

from server.app.runtime.job_runner import (
    _extract_requested_tool_name,
    _run_tool,
    _should_force_stage_progress,
)


class _Tool:
    def __init__(self, executor: dict):
        self.executor = executor


def test_extract_requested_tool_name() -> None:
    assert _extract_requested_tool_name("constraints.tool_name=read_file") == "read_file"
    assert _extract_requested_tool_name("nothing here") is None


def test_run_tool_builtin_execute_command(monkeypatch, tmp_path) -> None:
    from server.app.runtime import job_runner as job_runner_module

    monkeypatch.setattr(
        job_runner_module.tool_registry,
        "get",
        lambda _name: _Tool({"kind": "builtin", "name": "execute_command"}),
    )
    session = SimpleNamespace(workspace_path=str(tmp_path))
    job = SimpleNamespace(constraints={"command": "printf hello"})

    result = asyncio.run(_run_tool("execute_command", session, job))
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello"


def test_run_tool_dynamic_shell_executor(monkeypatch, tmp_path) -> None:
    from server.app.runtime import job_runner as job_runner_module

    monkeypatch.setattr(
        job_runner_module.tool_registry,
        "get",
        lambda _name: _Tool({"kind": "shell", "command": "printf {message}"}),
    )
    session = SimpleNamespace(workspace_path=str(tmp_path))
    job = SimpleNamespace(constraints={"message": "dynamic"})

    result = asyncio.run(_run_tool("custom_tool", session, job))
    assert result["exit_code"] == 0
    assert result["stdout"] == "dynamic"


def test_should_force_stage_progress_on_final_missing_signal() -> None:
    assert _should_force_stage_progress(
        signal="missing",
        cleaned_output="A useful answer",
        stage_attempt=3,
        max_stage_turns=3,
        repeated_count=0,
    )


def test_should_force_stage_progress_on_repeated_missing_signal() -> None:
    assert _should_force_stage_progress(
        signal="missing",
        cleaned_output="Same answer",
        stage_attempt=2,
        max_stage_turns=5,
        repeated_count=2,
    )


def test_should_not_force_stage_progress_when_needs_more() -> None:
    assert not _should_force_stage_progress(
        signal="needs_more",
        cleaned_output="Need more info",
        stage_attempt=3,
        max_stage_turns=3,
        repeated_count=2,
    )
