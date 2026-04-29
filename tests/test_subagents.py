import time

from core.subagents import SubAgentManager
from core.server import build_state_payload
from core.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse


class DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy-model"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="ok", parts=[], input_tokens=1, output_tokens=1, total_tokens=2)

    def upload_file(self, file_path, mime_type):
        return None


def test_subagent_manager_runs_tasks_and_refills_slots():
    manager = SubAgentManager(max_parallel=2)

    def worker(task):
        time.sleep(0.05)
        return {"status": "completed", "summary": f"done {task.title}"}

    _, ids = manager.submit_many([{"title": f"task-{i}", "payload": {"i": i}} for i in range(4)], worker)
    wait = manager.wait(ids, timeout_s=3)
    assert wait["counts"]["completed"] == 4


def test_subagent_cancel_and_timeout_states():
    manager = SubAgentManager(max_parallel=1, task_timeout_s=1)

    def slow_worker(task):
        time.sleep(2)
        return {"status": "completed", "summary": "slow"}

    wid = manager.submit("slow", {}, slow_worker)
    result = manager.wait([wid], timeout_s=2)
    worker = result["workers"][0]
    assert worker["status"] in {"timed_out", "completed"}

    w2 = manager.submit("cancel-me", {}, slow_worker)
    cancelled = manager.cancel([w2])
    assert cancelled >= 1


def test_session_subagent_counts_exposed_in_state_payload():
    sm = SessionManager(session_name="subagent-state-payload")
    session = Session(DummyProvider("dummy"), False, "system", sm)

    worker_id = session.submit_subagent_task(title="quick", prompt="do x")
    assert worker_id.startswith("sa-")

    timeout = time.time() + 3
    while time.time() < timeout:
        if session.get_subagent_counts().get("completed", 0) >= 1:
            break
        time.sleep(0.02)

    payload = build_state_payload(session)
    assert "subagents" in payload
    assert "subagent_counts" in payload
    assert isinstance(payload["subagents"], list)
    assert payload["subagent_counts"]["completed"] >= 1


def test_session_batch_submit_wait_and_cancel():
    sm = SessionManager(session_name="subagent-batch")
    session = Session(DummyProvider("dummy"), False, "system", sm)
    created = session.submit_subagent_batch([
        {"title": "one", "payload": {"prompt": "1"}},
        {"title": "two", "payload": {"prompt": "2"}},
    ])
    assert created["batch_id"].startswith("batch-")
    assert len(created["workers"]) == 2
    summary = session.wait_for_subagents(created["workers"], timeout_s=2)
    assert isinstance(summary.get("workers"), list)


def test_subagent_max_parallel_validation_clamps():
    sm = SessionManager(session_name="subagent-max-parallel")
    sm.variables["subagent_max_parallel"] = 999
    session = Session(DummyProvider("dummy"), False, "system", sm)
    assert session.get_subagent_counts() is not None
    assert session.subagent_manager.max_parallel == 16
