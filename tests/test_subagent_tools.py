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


def test_child_policy_profile_denies_filesystem_mutation_domain():
    sm = SessionManager(session_name="subagent-child-policy-fs")
    session = Session(DummyProvider("dummy"), False, "system", sm)
    vars_child = dict(session.variables)
    vars_child["subagent_policy_profile"] = "child"
    vars_child["subagent_child_allowed_domains"] = "read,memory"
    raw = execute_tool(
        "write_file",
        {"filename": "tmp.txt", "content": "x"},
        session.folder_context,
        None,
        vars_child,
        invocation_source="subagent_child",
        session=session,
    )
    payload = json.loads(raw)
    assert payload.get("ok") is False
    assert payload.get("error_code") == "policy_denied"


def test_spawn_sub_agents_auto_schedule_and_chunk_expansion(monkeypatch):
    sm = SessionManager(session_name="subagent-auto-schedule")
    session = Session(DummyProvider("dummy"), False, "system", sm)
    captured = {}

    def _fake_submit_subagent_batch(tasks, batch_id=None):
        captured["tasks"] = tasks
        return {"batch_id": "b1", "workers": ["w1", "w2", "w3"]}

    monkeypatch.setattr(session, "submit_subagent_batch", _fake_submit_subagent_batch)
    raw = execute_tool(
        "spawn_sub_agents",
        {
            "tasks": [
                {"title": "docs", "prompt": "update documentation"},
                {"title": "critical fix", "prompt": "regression blocker", "priority": "p0"},
                {"title": "refactor", "chunks": ["part one", "part two"]},
            ],
            "auto_schedule": True,
        },
        session.folder_context,
        None,
        session.variables,
        invocation_source="session",
        session=session,
    )
    payload = json.loads(raw)
    assert payload.get("ok") is True
    assert len(captured["tasks"]) == 4
    # Critical/p0 task should be ordered first when auto scheduling is enabled.
    assert captured["tasks"][0]["title"] == "critical fix"


def test_message_and_complete_sub_agent_tools():
    sm = SessionManager(session_name="subagent-interactive")
    session = Session(DummyProvider("dummy"), False, "system", sm)
    created = session.submit_subagent_task(title="interactive", prompt="initial")
    session.wait_for_subagents([created], timeout_s=2)

    raw_msg = execute_tool(
        "message_sub_agent",
        {"worker_id": created, "prompt": "follow up and refine"},
        session.folder_context,
        None,
        session.variables,
        invocation_source="session",
        session=session,
    )
    msg_payload = json.loads(raw_msg)
    assert msg_payload.get("ok") is True
    assert msg_payload.get("data", {}).get("worker_id") == created

    raw_complete = execute_tool(
        "complete_sub_agent",
        {"worker_id": created, "summary": "parent accepted"},
        session.folder_context,
        None,
        session.variables,
        invocation_source="session",
        session=session,
    )
    complete_payload = json.loads(raw_complete)
    assert complete_payload.get("ok") is True
