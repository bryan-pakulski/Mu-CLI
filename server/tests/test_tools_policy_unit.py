from server.app.policies.engine import policy_engine
from server.app.tools.registry import tool_registry


def test_tools_registry_and_policy_unit() -> None:
    tool = tool_registry.get("read_file")
    assert tool is not None
    decision = policy_engine.evaluate("interactive", tool)
    assert decision.decision == "allow"
