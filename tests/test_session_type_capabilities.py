from types import SimpleNamespace

from mu.tools.capabilities import (
    allowed_tools,
    filter_tools_for_session_type,
    is_tool_allowed,
    normalize_session_type,
)


def test_unknown_type_falls_back_to_workspace():
    assert normalize_session_type("invalid") == "workspace"
    assert allowed_tools("invalid") is None


def test_chat_filters_host_tools_but_keeps_artifacts_and_research():
    tools = [
        SimpleNamespace(name="bash"),
        SimpleNamespace(name="read_file"),
        SimpleNamespace(name="web_search"),
        SimpleNamespace(name="upload_artifact"),
    ]
    assert [item.name for item in filter_tools_for_session_type(tools, "chat")] == [
        "web_search",
        "upload_artifact",
    ]
    assert not is_tool_allowed("bash", "chat")
    assert is_tool_allowed("bash", "container")
