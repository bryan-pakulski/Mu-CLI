from pathlib import Path

from server.app.policies.engine import policy_engine
from server.app.tools.registry import ReadFileTool


def test_tools_registry_and_policy_unit(tmp_path: Path) -> None:
    assert policy_engine.evaluate("interactive", ReadFileTool(lambda: tmp_path)).decision == "allow"
