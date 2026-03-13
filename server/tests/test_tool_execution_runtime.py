import asyncio
from types import SimpleNamespace

from server.app.runtime.job_runner import (
    _citations_required,
    _extract_requested_tool_name,
    _extract_tool_calls,
    _run_tool,
    _should_force_stage_progress,
)


class _Tool:
    def __init__(self, executor: dict):
        self.executor = executor


def _session(tmp_path):
    return SimpleNamespace(
        id="session-1",
        workspace_path=str(tmp_path),
        context_state={"summary": "short", "messages": [{"role": "user", "content": "hi"}]},
    )


def test_extract_requested_tool_name() -> None:
    assert _extract_requested_tool_name("constraints.tool_name=read_file") == "read_file"
    assert _extract_requested_tool_name("nothing here") is None


def test_extract_tool_calls_from_xml_blocks() -> None:
    output = """<tool_call><tool_name>search_arxiv_papers</tool_name><parameters>{"query":"potato","max_results":3}</parameters></tool_call>
<tool_call><tool_name>write_file</tool_name><parameters>{"file_path":"note.md","content":"hello"}</parameters></tool_call>"""
    calls = _extract_tool_calls(output)
    assert len(calls) == 2
    assert calls[0]["tool_name"] == "search_arxiv_papers"
    assert calls[0]["constraints"]["query"] == "potato"
    assert calls[1]["tool_name"] == "write_file"
    assert calls[1]["constraints"]["file_path"] == "note.md"


def test_run_tool_builtin_execute_command(monkeypatch, tmp_path) -> None:
    from server.app.runtime import job_runner as job_runner_module

    monkeypatch.setattr(
        job_runner_module.tool_registry,
        "get",
        lambda _name: _Tool({"kind": "builtin", "name": "execute_command"}),
    )
    session = _session(tmp_path)
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
    session = _session(tmp_path)
    job = SimpleNamespace(constraints={"message": "dynamic"})

    result = asyncio.run(_run_tool("custom_tool", session, job))
    assert result["exit_code"] == 0
    assert result["stdout"] == "dynamic"


def test_remaining_builtin_tools_are_runnable(monkeypatch, tmp_path) -> None:
    from server.app.runtime import job_runner as job_runner_module

    source_file = tmp_path / "a.txt"
    source_file.write_text("hello from file", encoding="utf-8")
    upload_dir = tmp_path / ".mu" / "uploaded_context"
    upload_dir.mkdir(parents=True)
    (upload_dir / "note.txt").write_text("uploaded note", encoding="utf-8")

    def fake_fetch(url: str, timeout_s: int = 20):
        if "arxiv" in url:
            return (
                "application/atom+xml",
                """<?xml version='1.0' encoding='UTF-8'?>
                <feed xmlns='http://www.w3.org/2005/Atom'>
                  <entry><id>id-1</id><title>T1</title><summary>S1</summary></entry>
                </feed>""",
            )
        return (
            "text/html",
            """<html><body>
            <a href='https://example.com/a'>A</a>
            <div class='result__a'>Result title</div>
            <div class='result__snippet'>Result snippet</div>
            <p>Body text</p>
            </body></html>""",
        )

    monkeypatch.setattr(job_runner_module, "_fetch_url", fake_fetch)

    checks = [
        ("read_file", {"file_path": "a.txt"}),
        ("write_file", {"file_path": "b.txt", "content": "new"}),
        ("list_workspace_files", {}),
        ("get_workspace_file_context", {"file_path": "a.txt"}),
        ("git", {"command": "status"}),
        ("apply_patch", {"patch": ""}),
        ("fetch_url_context", {"url": "https://example.com"}),
        ("fetch_pdf_context", {"url": "https://example.com/file.pdf"}),
        ("extract_links_context", {"url": "https://example.com"}),
        ("search_web_context", {"query": "mu cli"}),
        ("search_arxiv_papers", {"query": "llm"}),
        ("score_sources", {"sources": [{"title": "arxiv", "summary": "x" * 120}]}),
        ("run_make_agent_job", {"goal": "nested"}),
        ("list_uploaded_context_files", {}),
        ("get_uploaded_context_file", {"name": "note.txt"}),
        ("clear_uploaded_context_store", {}),
        ("retrieve_conversation_summary", {}),
    ]

    for tool_name, constraints in checks:
        monkeypatch.setattr(
            job_runner_module.tool_registry,
            "get",
            lambda _name, tool_name=tool_name: _Tool(
                {"kind": "builtin", "name": tool_name}
            ),
        )
        result = asyncio.run(_run_tool(tool_name, _session(tmp_path), SimpleNamespace(constraints=constraints)))
        assert result["tool_name"] == tool_name
        assert result.get("status") != "not_implemented"


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


def test_citations_required_in_research_mode() -> None:
    assert _citations_required("research", None, {"read_file"})


def test_citations_required_with_internet_tool_enabled() -> None:
    assert _citations_required("interactive", ["search_web_context"], {"read_file", "search_web_context"})


def test_citations_not_required_for_local_only_tools() -> None:
    assert not _citations_required("interactive", ["read_file", "write_file"], {"read_file", "write_file"})


def test_run_tool_uses_call_constraints_over_job_constraints(monkeypatch, tmp_path) -> None:
    from server.app.runtime import job_runner as job_runner_module

    monkeypatch.setattr(
        job_runner_module.tool_registry,
        "get",
        lambda _name: _Tool({"kind": "builtin", "name": "write_file"}),
    )
    session = _session(tmp_path)
    job = SimpleNamespace(constraints={"file_path": "old.txt", "content": "old"})

    result = asyncio.run(
        _run_tool(
            "write_file",
            session,
            job,
            call_constraints={"file_path": "new.txt", "content": "new"},
        )
    )
    assert result["file_path"] == "new.txt"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "new"


def test_run_tool_read_write_supports_path_alias(monkeypatch, tmp_path) -> None:
    from server.app.runtime import job_runner as job_runner_module

    monkeypatch.setattr(
        job_runner_module.tool_registry,
        "get",
        lambda _name: _Tool({"kind": "builtin", "name": "write_file"}),
    )
    session = _session(tmp_path)
    write_job = SimpleNamespace(constraints={"path": "nested/note.txt", "content": "hello"})
    write_result = asyncio.run(_run_tool("write_file", session, write_job))
    assert write_result["path"] == "nested/note.txt"

    monkeypatch.setattr(
        job_runner_module.tool_registry,
        "get",
        lambda _name: _Tool({"kind": "builtin", "name": "read_file"}),
    )
    read_job = SimpleNamespace(constraints={"path": "nested/note.txt"})
    read_result = asyncio.run(_run_tool("read_file", session, read_job))
    assert read_result["content"] == "hello"


def test_run_tool_rejects_workspace_escape_paths(monkeypatch, tmp_path) -> None:
    from server.app.runtime import job_runner as job_runner_module

    monkeypatch.setattr(
        job_runner_module.tool_registry,
        "get",
        lambda _name: _Tool({"kind": "builtin", "name": "read_file"}),
    )
    session = _session(tmp_path)
    job = SimpleNamespace(constraints={"path": "../outside.txt"})
    result = asyncio.run(_run_tool("read_file", session, job))
    assert "outside attached workspace" in result["error"]
