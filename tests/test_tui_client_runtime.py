from pathlib import Path
import time

from tui.client_runtime import load_client_profile, run_client_loop, save_client_profile


class StubUI:
    def __init__(self, inputs):
        self.inputs = list(inputs)
        self.info_messages = []
        self.error_messages = []
        self.rendered = []
        self.prompted_choices = []

    def show_info(self, message):
        self.info_messages.append(str(message))

    def show_error(self, message):
        self.error_messages.append(str(message))

    def get_input(self, *args, **kwargs):
        if not self.inputs:
            raise EOFError
        return self.inputs.pop(0)

    def render_message(self, role, content, model_name=None):
        self.rendered.append((role, content, model_name))

    def prompt_choices(self, message, choices, default=None):
        self.prompted_choices.append((message, tuple(choices), default))
        return default or choices[0]

    def prompt(self, message, default=None):
        return default or "test-reason"


class StubClient:
    base_url = "http://127.0.0.1:8765"
    def __init__(self):
        self.client_id = "client-stub"
        self.task_statuses = {
            "t1": ["completed"],
            "t2": ["running", "running", "completed"],
        }
        self.resolutions = []

    def capabilities(self):
        return {"ok": True, "capabilities": {"server_runtime": "authoritative"}}

    def state(self):
        return {
            "ok": True,
            "state": {
                "current_session": "default",
                "staged_files": [],
                "runtime": {"agent_mode": "default"},
                "feature_state": {},
                "provider": {"model": "stub-model"},
            },
        }

    def tasks(self):
        return {"ok": True, "tasks": [{"task_id": "t1", "status": "completed", "kind": "message"}]}

    def task(self, task_id):
        states = self.task_statuses.get(task_id, ["completed"])
        status = states.pop(0) if len(states) > 1 else states[0]
        self.task_statuses[task_id] = states
        return {"ok": True, "task": {"task_id": task_id, "status": status}}

    def command(self, cmd):
        if cmd == "/quit":
            return {"ok": True, "data": {"exit": True}, "session_name": "default"}
        return {"ok": True, "message": "ok", "session_name": "default"}

    def message(self, text):
        return {"ok": True, "assistant_text": f"echo {text}"}

    def approvals(self):
        return {
            "ok": True,
            "pending_approvals": [
                {"approval_id": "a1", "task_id": "t9", "tool_name": "write_file"}
            ],
        }

    def resolve_approval(self, approval_id, decision, reason=None):
        self.resolutions.append((approval_id, decision, reason))
        return {
            "ok": True,
            "approval": {
                "approval_id": approval_id,
                "decision": decision,
                "reason": reason,
            },
        }

    def stream_events(self, *, task_id=None, max_events=None):
        if max_events is None and task_id == "tail":
            for _ in range(200):
                time.sleep(0.005)
                yield {"event": "trace.tool", "payload": {"tool_name": "tail"}}
            return
        events = [
            {"event": "trace.tool", "payload": {"tool_name": "read_file"}},
            {"event": "approval.requested", "payload": {"approval_id": "a1"}},
            {"event": "trace.tool_result", "payload": {"ok": True}},
        ]
        limit = max_events if max_events is not None else len(events)
        for event in events[:limit]:
            yield event

    def arbiter_status(self):
        return {"ok": True, "arbiter": {"lock_active": False, "owner_client_id": None}}

    def arbiter_claim(self, force=False):
        return {"ok": True, "arbiter": {"lock_active": True, "owner_client_id": self.base_url}}

    def arbiter_release(self):
        return {"ok": True, "arbiter": {"lock_active": False, "owner_client_id": None}}

    def arbiter_set_observer(self, enabled):
        return {
            "ok": True,
            "arbiter": {"lock_active": False, "owner_client_id": None},
            "observer": bool(enabled),
        }


def test_profile_round_trip(tmp_path):
    profile_path = tmp_path / "profile.json"
    save_client_profile({"server_url": "http://localhost:8765"}, profile_path)

    loaded = load_client_profile(profile_path)

    assert loaded["server_url"] == "http://localhost:8765"


def test_run_client_loop_supports_tasks_commands(tmp_path):
    ui = StubUI(inputs=["/tasks", "/task t1", "/quit"])

    run_client_loop(ui, StubClient(), remember_server=False)

    assert any("Tasks:" in msg for msg in ui.info_messages)
    assert any("Task t1 status=completed" in msg for msg in ui.info_messages)


def test_watch_command_polls_until_task_completes():
    ui = StubUI(inputs=["/watch t2", "/quit"])

    run_client_loop(ui, StubClient(), remember_server=False)

    assert any("status=running" in msg for msg in ui.info_messages)
    assert any("status=completed" in msg for msg in ui.info_messages)


def test_stream_and_approval_commands():
    client = StubClient()
    ui = StubUI(inputs=["/approvals", "/approve a1 approve", "/stream t2 3", "/quit"])

    run_client_loop(ui, client, remember_server=False)

    assert any("Pending approvals: 1" in msg for msg in ui.info_messages)
    assert any("Resolved a1 decision=approve" in msg for msg in ui.info_messages)
    assert any("[trace.tool]" in msg for msg in ui.info_messages)
    assert any("Approval a1 resolved via stream: approve" in msg for msg in ui.info_messages)
    assert ("a1", "approve", None) in client.resolutions


def test_live_stream_start_status_stop():
    ui = StubUI(inputs=["/stream live tail", "/stream status", "/stream stop", "/quit"])

    run_client_loop(ui, StubClient(), remember_server=False)

    assert any("Live stream started" in msg for msg in ui.info_messages)
    assert any("Live stream is running" in msg for msg in ui.info_messages)
    assert any("Live stream stopped." in msg for msg in ui.info_messages)


def test_lock_commands():
    ui = StubUI(
        inputs=[
            "/lock status",
            "/lock claim",
            "/lock observe on",
            "/lock observe off",
            "/lock release",
            "/quit",
        ]
    )

    run_client_loop(ui, StubClient(), remember_server=False)

    assert any("Lock active=" in msg for msg in ui.info_messages)
    assert any("Lock claimed by" in msg for msg in ui.info_messages)
    assert any("Observer mode enabled" in msg for msg in ui.info_messages)
    assert any("Observer mode disabled" in msg for msg in ui.info_messages)
    assert any("Lock released." in msg for msg in ui.info_messages)
