import pytest

pytest.importorskip("sqlalchemy")

from pathlib import Path

from server.app.policies.engine import policy_engine
from server.app.tools.registry import tool_registry
from server.app.workspace.discovery import _classify, _describe, _priority, _should_skip


def test_policy_engine_decisions() -> None:
    low = tool_registry.get("read_file")
    medium = tool_registry.get("write_file")
    high = tool_registry.get("run_make_agent_job")

    assert low is not None and medium is not None and high is not None

    assert policy_engine.evaluate("interactive", low).decision == "allow"
    assert policy_engine.evaluate("interactive", medium).decision == "ask"
    assert policy_engine.evaluate("interactive", high).decision == "ask"
    assert policy_engine.evaluate("yolo", medium).decision == "allow"
    assert policy_engine.evaluate("yolo", high).decision == "escalate"


def test_workspace_helpers() -> None:
    file_type, lang = _classify(Path("src/main.py"))
    assert (file_type, lang) == ("code", "py")

    file_type, lang = _classify(Path("README.md"))
    assert (file_type, lang) == ("docs", "md")

    assert _describe(Path("README.md"), "docs") == "Repository or module readme"
    assert _describe(Path("tests/test_api.py"), "code") == "Test file"

    assert _priority("README.md", "docs", ["docs"]) > _priority(
        "tests/test_api.py", "code", ["code"]
    )

    assert _should_skip(Path("/workspace/tools/.git/config"))
    assert not _should_skip(Path("/workspace/tools/server/app/main.py"))
