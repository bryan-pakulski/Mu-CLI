"""Container sessions use Docker, not FolderContext, as their access boundary."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from mu.agent.hooks import HookContext, HookRegistry
from mu.agent.secret_guard import install as install_secret_guard
from mu.tools._bounds import check_bounds
from mu.tools.file.handlers import write_file
from mu.tools.shell.handlers import bash_command
from mu.tools.workspace.handlers import (
    list_dir,
    read_file,
    retrieve_relevant_context,
    search_for_string,
    search_references,
)
from mu.workspace.folder_context import FolderContext


def _workspace(root: Path) -> FolderContext:
    context = FolderContext()
    context.add_folder(str(root))
    return context


def test_container_paths_are_not_confined_to_attached_workspace(tmp_path):
    attached = tmp_path / "attached"
    attached.mkdir()
    outside = tmp_path / "system-area"
    outside.mkdir()
    context = _workspace(attached)

    assert check_bounds(str(outside), context) is False
    assert check_bounds(str(outside), context, session_type="container") is True


def test_container_file_tools_can_read_and_write_outside_workspace(tmp_path):
    attached = tmp_path / "attached"
    attached.mkdir()
    outside = tmp_path / "system-area"
    outside.mkdir()
    context = _workspace(attached)
    target = outside / "runtime.conf"

    result = write_file(
        str(target),
        "enabled=true\n",
        context,
        session_type="container",
    )
    assert "Successfully wrote" in result
    assert (
        read_file(str(target), context, session_type="container")
        == "enabled=true\n"
    )


def test_container_bash_accepts_arbitrary_non_secret_cwd(tmp_path):
    attached = tmp_path / "attached"
    attached.mkdir()
    outside = tmp_path / "system-area"
    outside.mkdir()
    context = _workspace(attached)

    denied = bash_command("pwd", context, cwd=str(outside))
    allowed = bash_command(
        "pwd",
        context,
        cwd=str(outside),
        session_type="container",
    )

    assert "Access denied" in denied
    assert str(outside) in allowed
    assert "Exit code: 0" in allowed


def test_container_bash_does_not_require_a_workspace(tmp_path):
    result = bash_command(
        "printf container-ready",
        None,
        cwd=str(tmp_path),
        session_type="container",
    )

    assert "container-ready" in result
    assert "Exit code: 0" in result


def test_container_search_accepts_explicit_runtime_root(tmp_path):
    attached = tmp_path / "attached"
    attached.mkdir()
    outside = tmp_path / "opt" / "package"
    outside.mkdir(parents=True)
    (outside / "module.py").write_text("RUNTIME_MARKER = True\n", encoding="utf-8")
    context = _workspace(attached)

    exact = search_for_string(
        "RUNTIME_MARKER",
        context,
        path=str(outside),
        session_type="container",
    )
    refs = json.loads(
        search_references(
            "RUNTIME_MARKER",
            context,
            path=str(outside),
            session_type="container",
        )
    )

    assert "module.py:1" in exact
    assert refs["count"] == 1
    assert refs["results"][0]["filepath"].endswith("module.py")


def test_container_semantic_retrieval_can_index_arbitrary_root(tmp_path):
    attached = tmp_path / "attached"
    attached.mkdir()
    outside = tmp_path / "usr-local-src"
    outside.mkdir()
    (outside / "service.py").write_text(
        "def container_runtime_probe():\n    return 'reachable'\n",
        encoding="utf-8",
    )
    context = _workspace(attached)

    payload = json.loads(
        retrieve_relevant_context(
            "container runtime probe",
            context,
            path=str(outside),
            session_type="container",
        )
    )

    assert payload["ok"] is True
    assert payload["count"] >= 1
    assert any(item["path"].endswith("service.py") for item in payload["results"])


def test_container_directory_listing_hides_secret_entries(tmp_path):
    attached = tmp_path / "attached"
    attached.mkdir()
    outside = tmp_path / "etc-app"
    outside.mkdir()
    (outside / "normal.conf").write_text("ok", encoding="utf-8")
    (outside / ".env").write_text("API_KEY=never-show", encoding="utf-8")
    context = _workspace(attached)

    result = list_dir(str(outside), context, session_type="container")

    assert "normal.conf" in result
    assert ".env" not in result


def test_container_secret_guard_is_not_bypassable():
    registry = HookRegistry()
    install_secret_guard(registry)
    context = HookContext(
        point="pre_tool",
        tool_name="bash",
        tool_args={"command": "cat ~/.ssh/id_rsa"},
        variables={
            "session_type": "container",
            "security_allow_secret_paths": True,
        },
        session=SimpleNamespace(
            variables={
                "session_type": "container",
                "security_allow_secret_paths": True,
            }
        ),
    )

    result = registry.first_short_circuit("pre_tool", context)

    assert result is not None
    assert result.payload["error_code"] == "secret_guard_blocked"
