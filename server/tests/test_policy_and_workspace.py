import pytest

pytest.importorskip("sqlalchemy")

from pathlib import Path

from server.app.policies.engine import policy_engine
from server.app.tools.registry import (
    GetWorkspaceFileContextTool,
    ListWorkspaceFilesTool,
    MakefileAgentTool,
    ReadFileTool,
    WriteFileTool,
)
from server.app.workspace.discovery import WorkspaceStore


def test_policy_engine_decisions(tmp_path: Path) -> None:
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()

    store = WorkspaceStore(tmp_path / ".workspace-store")
    store.attach(workspace_root)

    low = ReadFileTool(lambda: workspace_root)
    medium = WriteFileTool(lambda: workspace_root)
    high = MakefileAgentTool(lambda: workspace_root)

    assert policy_engine.evaluate("interactive", low).decision == "allow"
    assert policy_engine.evaluate("interactive", medium).decision == "ask"
    assert policy_engine.evaluate("interactive", high).decision == "ask"
    assert policy_engine.evaluate("yolo", medium).decision == "allow"
    assert policy_engine.evaluate("yolo", high).decision == "escalate"


def test_workspace_store_indexes_and_reads_files(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    (root / "README.md").write_text("# Demo\nhello\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text(
        "def hello():\n    return 'world'\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_api.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )

    store = WorkspaceStore(tmp_path / ".workspace-index")
    snapshot = store.attach(root)

    assert snapshot.root == str(root.resolve())
    assert len(snapshot.files) == 3

    paths = [item.path for item in snapshot.files]
    assert "README.md" in paths
    assert "src/main.py" in paths
    assert "tests/test_api.py" in paths

    readme = next(item for item in snapshot.files if item.path == "README.md")
    test_file = next(item for item in snapshot.files if item.path == "tests/test_api.py")

    assert "documentation" in readme.purpose.lower() or "readme" in readme.purpose.lower()
    assert "test" in test_file.purpose.lower()

    context = store.get_file_context("src/main.py")
    assert "def hello()" in context


def test_workspace_store_list_files_filters_by_query(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (root / "src" / "util.py").write_text("def util():\n    pass\n", encoding="utf-8")

    store = WorkspaceStore(tmp_path / ".workspace-index")
    store.attach(root)

    results = store.list_files(query="main", limit=10)
    assert len(results) == 1
    assert results[0].path == "src/main.py"


def test_workspace_store_respects_gitignore_and_excluded_dirs(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    (root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (root / "kept.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "ignored.py").write_text("print('ignore')\n", encoding="utf-8")

    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")

    store = WorkspaceStore(tmp_path / ".workspace-index")
    snapshot = store.attach(root)

    paths = [item.path for item in snapshot.files]
    assert "kept.py" in paths
    assert "ignored.py" not in paths
    assert ".git/config" not in paths

    assert snapshot.index_stats.get("ignored_by_gitignore", 0) >= 1
    assert snapshot.index_stats.get("excluded_dirs", 0) >= 1


def test_workspace_tools_use_attached_store(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "README.md").write_text("# Demo\nhello\n", encoding="utf-8")

    store = WorkspaceStore(tmp_path / ".workspace-index")
    store.attach(root)

    list_tool = ListWorkspaceFilesTool(store)
    result = list_tool.run({"limit": 10})
    assert result.ok is True
    assert "README.md" in result.output

    context_tool = GetWorkspaceFileContextTool(store)
    result = context_tool.run({"path": "README.md", "max_chars": 100})
    assert result.ok is True
    assert "# Demo" in result.output


def test_workspace_store_missing_file_returns_helpful_message(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    store = WorkspaceStore(tmp_path / ".workspace-index")
    store.attach(root)

    message = store.get_file_context("missing.py")
    assert message.startswith("Path not indexed in workspace:")
    assert "Closest matches:" in message or "missing.py" in message
