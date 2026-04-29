import json

from core.session import Session, SessionManager
from core.tools import execute_tool
from providers.base import LLMProvider, ProviderResponse


class DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy-model"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="ok", parts=[], input_tokens=1, output_tokens=1, total_tokens=2)

    def upload_file(self, file_path, mime_type):
        return None


def test_spawn_list_cancel_subagent_tools():
    sm = SessionManager(session_name="subagent-tools")
    session = Session(DummyProvider("dummy"), False, "system", sm)

    raw = execute_tool(
        "spawn_sub_agents",
        {"tasks": [{"title": "t1", "prompt": "do one"}], "wait_for_completion": True, "timeout_s": 2},
        session.folder_context,
        None,
        session.variables,
        invocation_source="session",
        session=session,
    )
    payload = json.loads(raw)
    assert payload.get("ok") is True
    workers = payload.get("data", {}).get("workers", [])
    assert workers

    raw_list = execute_tool(
        "list_sub_agents",
        {},
        session.folder_context,
        None,
        session.variables,
        invocation_source="session",
        session=session,
    )
    listed = json.loads(raw_list)
    assert listed.get("ok") is True

    raw_cancel = execute_tool(
        "cancel_sub_agents",
        {"worker_ids": workers},
        session.folder_context,
        None,
        session.variables,
        invocation_source="session",
        session=session,
    )
    cancelled = json.loads(raw_cancel)
    assert cancelled.get("ok") is True

    raw_retry = execute_tool(
        "retry_sub_agents",
        {"worker_ids": workers},
        session.folder_context,
        None,
        session.variables,
        invocation_source="session",
        session=session,
    )
    retried = json.loads(raw_retry)
    assert retried.get("ok") is True

    raw_timeline = execute_tool(
        "get_subagent_timeline",
        {"limit": 10},
        session.folder_context,
        None,
        session.variables,
        invocation_source="session",
        session=session,
    )
    timeline = json.loads(raw_timeline)
    assert timeline.get("ok") is True

    raw_integrate = execute_tool(
        "integrate_sub_agent_outputs",
        {},
        session.folder_context,
        None,
        session.variables,
        invocation_source="session",
        session=session,
    )
    integrated = json.loads(raw_integrate)
    assert integrated.get("ok") is True


def test_child_policy_profile_denies_orchestration_domain():
    sm = SessionManager(session_name="subagent-child-policy")
    session = Session(DummyProvider("dummy"), False, "system", sm)
    vars_child = dict(session.variables)
    vars_child["subagent_policy_profile"] = "child"
    raw = execute_tool(
        "spawn_sub_agents",
        {"tasks": [{"title": "blocked", "prompt": "x"}]},
        session.folder_context,
        None,
        vars_child,
        invocation_source="subagent_child",
        session=session,
    )
    payload = json.loads(raw)
    assert payload.get("ok") is False
    assert payload.get("error_code") == "policy_denied"
