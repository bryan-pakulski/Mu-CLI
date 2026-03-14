import asyncio
from pathlib import Path
from types import SimpleNamespace

from server.app.runtime.job_runner import (
    _citations_required,
    _extract_requested_tool_name,
    _extract_tool_calls,
    _run_tool,
    _should_force_stage_progress,
)
from server.app.workspace.discovery import WorkspaceStore


def _session(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)

    store = WorkspaceStore(tmp_path / ".workspace-index")
    store.attach(root)

    return SimpleNamespace(
        id="session-1",
        workspace=store,
        context_state={"summary": "short", "messages": [{"role": "user", "content": "hi"}]},
    )


def _job(**constraints):
    return SimpleNamespace(constraints=constraints)


def test_extract_requested_tool_name() -> None:
    assert _extract_requested_tool_name("constraints.tool_name=read_file") == "read_file"
    assert _extract_requested_tool_name("nothing here") is None


def test_extract_tool_calls_from_xml_blocks() -> None:
    output = """<tool_call><tool_name>search_arxiv_papers</tool_name><parameters>{"query":"potato","max_results":3}</parameters></tool_call>
<tool_call><tool_name>write_file</tool_name><parameters>{"path":"note.md","content":"hello"}</parameters></tool_call>"""
    calls = _extract_tool_calls(output)
    assert len(calls) == 2
    assert calls[0]["tool_name"] == "search_arxiv_papers"
    assert calls[0]["constraints"]["query"] == "potato"
    assert calls[1]["tool_name"] == "write_file"
    assert calls[1]["constraints"]["path"] == "note.md"


def test_run_tool_write_file(tmp_path: Path) -> None:
    session = _session(tmp_path)
    job = _job(path="note.txt", content="hello")

    result = asyncio.run(_run_tool("write_file", session, job))
    assert result["tool_name"] == "write_file"
    assert result["ok"] is True
    assert "Wrote file:" in result["output"]

    workspace_root = Path(session.workspace.snapshot.root)
    assert (workspace_root / "note.txt").read_text(encoding="utf-8") == "hello"


def test_run_tool_read_file(tmp_path: Path) -> None:
    session = _session(tmp_path)
    workspace_root = Path(session.workspace.snapshot.root)
    (workspace_root / "a.txt").write_text("hello from file", encoding="utf-8")

    result = asyncio.run(_run_tool("read_file", session, _job(path="a.txt")))
    assert result["tool_name"] == "read_file"
    assert result["ok"] is True
    assert result["output"] == "hello from file"


def test_remaining_registry_tools_are_runnable(monkeypatch, tmp_path: Path) -> None:
    session = _session(tmp_path)
    workspace_root = Path(session.workspace.snapshot.root)

    (workspace_root / "a.txt").write_text("hello from file", encoding="utf-8")
    upload_dir = workspace_root / ".mu" / "uploaded_context" / session.id
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "note.txt").write_text("uploaded note", encoding="utf-8")
    (workspace_root / "Makefile.agent").write_text("help:\n\t@echo ok\n", encoding="utf-8")

    checks = [
        ("read_file", {"path": "a.txt"}),
        ("write_file", {"path": "b.txt", "content": "new"}),
        ("list_workspace_files", {}),
        ("get_workspace_file_context", {"path": "a.txt"}),
        ("apply_patch", {"patch": ""}),
        ("fetch_url_context", {"url": "https://example.com"}),
        ("fetch_pdf_context", {"url": "https://example.com/file.pdf"}),
        ("extract_links_context", {"url": "https://example.com"}),
        ("search_web_context", {"query": "mu cli"}),
        ("search_arxiv_papers", {"query": "llm"}),
        ("score_sources", {"sources": [{"url": "https://arxiv.org/abs/1234.5678", "title": "arxiv", "snippet": "x" * 120}]}),
        ("run_make_agent_job", {}),
        ("list_uploaded_context_files", {}),
        ("get_uploaded_context_file", {"name": "note.txt"}),
        ("clear_uploaded_context_store", {}),
    ]

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    from server.app.runtime import job_runner as job_runner_module

    monkeypatch.setattr(job_runner_module.asyncio, "to_thread", fake_to_thread)

    for tool_name, constraints in checks:
        result = asyncio.run(_run_tool(tool_name, session, _job(**constraints)))
        assert result["tool_name"] == tool_name
        assert "tool not found" not in str(result)

    assert not (upload_dir / "note.txt").exists()


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
    assert _citations_required(
        "interactive",
        ["search_web_context"],
        {"read_file", "search_web_context"},
    )


def test_citations_not_required_for_local_only_tools() -> None:
    assert not _citations_required(
        "interactive",
        ["read_file", "write_file"],
        {"read_file", "write_file"},
    )


def test_run_tool_uses_call_constraints_over_job_constraints(tmp_path: Path) -> None:
    session = _session(tmp_path)
    job = _job(path="old.txt", content="old")

    result = asyncio.run(
        _run_tool(
            "write_file",
            session,
            job,
            call_constraints={"path": "new.txt", "content": "new"},
        )
    )

    assert result["tool_name"] == "write_file"
    assert result["ok"] is True

    workspace_root = Path(session.workspace.snapshot.root)
    assert (workspace_root / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (workspace_root / "old.txt").exists()


def test_run_tool_read_write_supports_path_key(tmp_path: Path) -> None:
    session = _session(tmp_path)

    write_job = _job(path="nested/note.txt", content="hello")
    write_result = asyncio.run(_run_tool("write_file", session, write_job))
    assert write_result["ok"] is True

    read_job = _job(path="nested/note.txt")
    read_result = asyncio.run(_run_tool("read_file", session, read_job))
    assert read_result["ok"] is True
    assert read_result["output"] == "hello"


def test_run_tool_rejects_workspace_escape_paths(tmp_path: Path) -> None:
    session = _session(tmp_path)
    job = _job(path="../outside.txt")

    result = asyncio.run(_run_tool("read_file", session, job))
    assert result["ok"] is False
    assert "outside attached workspace" in result["error"]
