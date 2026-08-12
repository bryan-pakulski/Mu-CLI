"""Tests for the async sub-agent lifecycle: stuck/stall detection, runtime
auto-kill, cooperative kill, LAYER 3B role injection, and the single-spawn
progress panel.

These exercise the new orchestrator contract:
  * ``spawn_agent`` dispatches to a background thread and returns a task_id.
  * ``poll_subagent`` / ``registry.snapshot`` report lifecycle signals.
  * ``kill_subagent`` cooperatively cancels and returns partial findings.
  * LAYER 3B is injected only when ``session_role`` is set.
"""

import io
import threading
import time
from types import SimpleNamespace

import pytest

from mu.agent.lifecycle import SubagentLifecycleManager
from mu.agent.registry import SubagentRegistry
from mu.session.context import inject_hierarchical_context
from mu.session.session import Session, SessionManager
from mu.workspace.folder_context import FolderContext
from mu.tools import build_tool_context, execute
from providers.base import LLMProvider, MessagePart, ProviderResponse


# ----------------------------------------------------------- test providers


class _ScriptedProvider(LLMProvider):
    """Queued ProviderResponses. Captures are shared via a dict so the
    shallow copy made by ``clone_for_child`` sees the child's writes."""

    def __init__(self, responses, delay: float = 0.0):
        super().__init__("scripted-model")
        self.name = "scripted"
        self.queue = list(responses)
        self._captures = {"system_prompt": None, "tool_names": None}
        self.delay = delay

    def get_available_models(self):
        return ["scripted-model"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        if self.delay:
            time.sleep(self.delay)
        self._captures["system_prompt"] = system_prompt or ""
        self._captures["tool_names"] = (
            [t.name for t in tools] if tools is not None else None
        )
        if not self.queue:
            return ProviderResponse(text="(empty queue)", parts=[])
        return self.queue.pop(0)

    def upload_file(self, *a, **kw):
        return None


def _read_file_call(target):
    return ProviderResponse(
        text="",
        parts=[
            MessagePart(
                type="tool_call",
                tool_name="read_file",
                tool_args={"filename": str(target)},
            )
        ],
    )


# ----------------------------------------------------------- helpers


def _build_parent(tmp_path, provider, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager()
    parent = Session(provider, False, "system", sm)
    fc = FolderContext()
    fc.add_folder(str(tmp_path))
    parent.folder_context = fc
    return parent


def _ctx_for(parent):
    return build_tool_context(
        folder_context=parent.folder_context,
        ui=None,
        variables=parent.variables,
        session=parent,
    )


def _poll_until(parent, task_id, target=("done", "killed", "error"), timeout=15.0):
    registry = parent._subagent_registry
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = registry.snapshot(task_id)
        if snap.get("status") in target:
            return snap
        time.sleep(0.02)
    return registry.snapshot(task_id)


def _render_panel_text(tracker) -> str:
    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=False, color_system=None)
    console.print(tracker.render_panel())
    return buf.getvalue()


# ----------------------------------------------------------- stuck detection


def test_lifecycle_detects_stuck_repeated_tool(tmp_path, monkeypatch):
    """A child calling the same tool + same args N times in a row triggers
    the stuck signal (stuck_threshold=3)."""
    target = tmp_path / "stable.txt"
    target.write_text("same content")

    provider = _ScriptedProvider(
        responses=[
            _read_file_call(target),
            _read_file_call(target),
            _read_file_call(target),
            _read_file_call(target),
            ProviderResponse(
                text="done after repeats",
                parts=[MessagePart(type="text", text="done after repeats")],
            ),
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    # Stuck fires at 3 repeats; keep stall out of the way.
    parent.variables["subagent_stuck_threshold"] = 3
    parent.variables["subagent_stall_threshold"] = 50

    res = execute("spawn_agent", {"task": "repeat"}, _ctx_for(parent))
    snap = _poll_until(parent, res["data"]["task_id"])

    assert snap["status"] == "done"
    assert snap["stuck"] is True, f"expected stuck, snap={snap}"
    assert snap["consecutive_repeats"] >= 3


# ----------------------------------------------------------- stall detection


def test_lifecycle_detects_stall_no_novel_output(tmp_path, monkeypatch):
    """A child whose tool calls produce no novel output (identical results)
    triggers the stall signal (stall_threshold=2) without triggering stuck
    (stuck_threshold kept high)."""
    target = tmp_path / "stable.txt"
    target.write_text("same content")

    provider = _ScriptedProvider(
        responses=[
            _read_file_call(target),
            _read_file_call(target),
            _read_file_call(target),
            ProviderResponse(
                text="done after stalls",
                parts=[MessagePart(type="text", text="done after stalls")],
            ),
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent.variables["subagent_stuck_threshold"] = 50  # suppress stuck
    parent.variables["subagent_stall_threshold"] = 2  # stall at 2 non-novel

    res = execute("spawn_agent", {"task": "stall"}, _ctx_for(parent))
    snap = _poll_until(parent, res["data"]["task_id"])

    assert snap["status"] == "done"
    assert snap["stall"] is True, f"expected stall, snap={snap}"
    assert snap["stuck"] is False
    assert snap["consecutive_stalls"] >= 2


# ----------------------------------------------------------- runtime auto-kill


def test_lifecycle_runtime_watchdog_auto_kills(tmp_path, monkeypatch):
    """A child that runs past subagent_max_runtime_seconds is auto-killed
    with kill_reason='runtime_exceeded' and a non-empty partial summary."""
    target = tmp_path / "slow.txt"
    target.write_text("payload")

    # ~0.3s per generate, 6 tool calls -> ~1.8s+, well past the 1s budget.
    provider = _ScriptedProvider(
        responses=[_read_file_call(target) for _ in range(6)]
        + [
            ProviderResponse(
                text="should not reach",
                parts=[MessagePart(type="text", text="should not reach")],
            )
        ],
        delay=0.3,
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    parent.variables["subagent_max_runtime_seconds"] = 1

    res = execute("spawn_agent", {"task": "run too long"}, _ctx_for(parent))
    assert res["data"]["status"] == "running"
    snap = _poll_until(parent, res["data"]["task_id"], target=("killed",), timeout=10.0)

    assert snap["status"] == "killed", f"expected killed, snap={snap}"
    assert snap["kill_reason"] == "runtime_exceeded"
    # Partial findings were recovered from the history of tool calls made
    # before the watchdog fired.
    assert snap["summary"]
    assert "Partial findings" in snap["summary"] or "payload" in snap["summary"]


# ----------------------------------------------------------- cooperative kill


def test_kill_subagent_returns_partial_findings(tmp_path, monkeypatch):
    """kill_subagent mid-run cancels the child and returns whatever it had
    produced so far."""
    target = tmp_path / "killtarget.txt"
    target.write_text("partial payload")

    # Slow child: ~0.3s/generate, several tool calls so it is still running
    # when we kill it.
    provider = _ScriptedProvider(
        responses=[_read_file_call(target) for _ in range(6)]
        + [
            ProviderResponse(
                text="should not reach",
                parts=[MessagePart(type="text", text="should not reach")],
            )
        ],
        delay=0.3,
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)

    res = execute("spawn_agent", {"task": "will be killed"}, _ctx_for(parent))
    task_id = res["data"]["task_id"]

    # Let the child complete at least one tool call so partial findings exist.
    time.sleep(0.5)
    kill_res = execute("kill_subagent", {"task_id": task_id}, _ctx_for(parent))

    assert kill_res["ok"] is True
    data = kill_res["data"]
    assert data["status"] == "killed", f"expected killed, data={data}"
    assert data["kill_reason"] == "killed_by_parent"
    assert data["summary"]
    assert "partial payload" in data["summary"]


# ----------------------------------------------------------- LAYER 3B roles


def test_layer_3b_gating_and_roles(tmp_path, monkeypatch):
    """LAYER 3B is skipped for single-agent sessions; parent gets
    ORCHESTRATOR guidance, child gets SUB-AGENT guidance with depth."""

    def _prompt(session):
        return inject_hierarchical_context(session, "base system prompt")

    # Single-agent session: session_role unset -> no LAYER 3B.
    parent = _build_parent(tmp_path, _ScriptedProvider([]), monkeypatch)
    parent.variables["session_role"] = ""
    single = _prompt(parent)
    assert "ORCHESTRATOR" not in single
    assert "SUB-AGENT" not in single

    # Parent orchestrator role.
    parent.variables["session_role"] = "parent"
    parent_prompt = _prompt(parent)
    assert "ORCHESTRATOR" in parent_prompt
    assert "poll_subagent" in parent_prompt
    assert "kill_subagent" in parent_prompt

    # Child sub-agent role with depth.
    child = _build_parent(tmp_path, _ScriptedProvider([]), monkeypatch)
    child.variables["session_role"] = "child"
    child.variables["subagent_depth"] = 1
    child_prompt = _prompt(child)
    assert "SUB-AGENT" in child_prompt
    assert "depth=1" in child_prompt
    # A depth=1 child may spawn one more level.
    assert "spawn up to 1 further" in child_prompt

    # Depth-cap child: no further spawning.
    capped = _build_parent(tmp_path, _ScriptedProvider([]), monkeypatch)
    capped.variables["session_role"] = "child"
    capped.variables["subagent_depth"] = 2
    capped_prompt = _prompt(capped)
    assert "depth cap reached" in capped_prompt


# ----------------------------------------------------------- single-spawn panel


def test_single_spawn_renders_parent_and_child_rows(tmp_path, monkeypatch):
    """Success criterion 3: a single spawn renders the progress panel (the
    old spawn_count>=2 gate is gone). While the child runs, the tracker has
    one child row and render_panel adds a parent orchestrator row."""
    target = tmp_path / "panel.txt"
    target.write_text("payload")

    # Slow enough to observe the running state before it finishes.
    provider = _ScriptedProvider(
        responses=[_read_file_call(target) for _ in range(3)]
        + [
            ProviderResponse(
                text="panel done",
                parts=[MessagePart(type="text", text="panel done")],
            )
        ],
        delay=0.2,
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)

    res = execute("spawn_agent", {"task": "single spawn"}, _ctx_for(parent))
    assert res["data"]["status"] == "running"

    tracker = parent._subagent_registry.tracker
    # While the child is running: one child row, panel is active, and the
    # parent orchestrator row is rendered.
    snap = tracker.snapshot()
    assert len(snap) == 1
    assert snap[0].status in ("running", "stuck")
    assert tracker.has_active() is True

    panel_text = _render_panel_text(tracker)
    assert "(parent)" in panel_text
    assert "orchestrating" in panel_text

    # Let it finish and confirm the child row closes as done.
    final = _poll_until(parent, res["data"]["task_id"])
    assert final["status"] == "done"
    rows = tracker.snapshot()
    assert any(r.status == "done" for r in rows)
    assert tracker.has_active() is False


# ----------------------------------------------------------- GUI emit plane


def _make_registry_with_publish():
    """A registry whose ``_publish`` collects events into a list."""
    reg = SubagentRegistry()
    events: list = []
    reg._publish = lambda ev: events.append(ev)
    return reg, events


def _fake_child():
    return SimpleNamespace(
        session_manager=SimpleNamespace(history=[]),
        _subagent_cancelled=False,
        _subagent_kill_reason=None,
    )


def test_registry_emits_start_and_end_when_publish_set():
    """register() emits subagent_start; _close_tracker() emits subagent_end.
    Both carry the task_id the GUI panel upserts by."""
    reg, events = _make_registry_with_publish()
    lc = SubagentLifecycleManager(thresholds={"enabled": False})
    record = reg.register(
        _fake_child(), task="review x", depth=1, lifecycle=lc, model="qwen3"
    )
    starts = [e for e in events if e.get("kind") == "subagent_start"]
    assert len(starts) == 1
    assert starts[0]["task_id"] == record.task_id
    assert starts[0]["task"] == "review x"
    assert starts[0]["depth"] == 1
    assert starts[0]["model"] == "qwen3"

    reg._close_tracker(
        record, tool_count=4, summary="done", error=None, status="done"
    )
    ends = [e for e in events if e.get("kind") == "subagent_end"]
    assert len(ends) == 1
    assert ends[0]["task_id"] == record.task_id
    assert ends[0]["status"] == "done"
    assert ends[0]["tool_calls"] == 4


def test_registry_silent_when_publish_unset():
    """CLI/TUI: no _publish attached → register / _close_tracker emit nothing
    and never raise."""
    reg = SubagentRegistry()
    lc = SubagentLifecycleManager(thresholds={"enabled": False})
    record = reg.register(_fake_child(), task="t", depth=1, lifecycle=lc)
    reg._close_tracker(record, tool_count=0, summary="", error=None, status="done")
    # No exception, no publish callback to observe — the contract is "no-op".
    assert reg._publish is None


def test_registry_tracker_emits_progress_per_tool():
    """The tracker emits subagent_progress on update_tool once its row is
    linked to a task_id (done in register). Carries tool_count + last_tool."""
    reg, events = _make_registry_with_publish()
    lc = SubagentLifecycleManager(thresholds={"enabled": False})
    record = reg.register(_fake_child(), task="t", depth=1, lifecycle=lc)
    aid = record.tracker_agent_id
    assert aid is not None
    reg.tracker.update_tool(aid, "read_file")
    reg.tracker.update_tool(aid, "search_for_string")
    progress = [e for e in events if e.get("kind") == "subagent_progress"]
    assert len(progress) >= 2
    assert progress[-1]["task_id"] == record.task_id
    assert progress[-1]["tool_count"] == 2
    assert progress[-1]["last_tool"] == "search_for_string"


def test_progress_events_are_self_describing_and_snapshot_keeps_actions():
    reg, events = _make_registry_with_publish()
    lc = SubagentLifecycleManager(thresholds={"enabled": False})
    record = reg.register(
        _fake_child(),
        task="Inspect authentication middleware",
        title="Auth inspection",
        depth=2,
        lifecycle=lc,
        model="qwen3",
        specialist_key="code_research",
        max_iterations=40,
    )
    reg.tracker.update_tool(record.tracker_agent_id, "read_file", "auth.py")
    reg.tracker.complete_tool(record.tracker_agent_id, "read_file", ok=True)

    progress = [e for e in events if e.get("kind") == "subagent_progress"]
    assert progress[-1]["task"] == "Inspect authentication middleware"
    assert progress[-1]["title"] == "Auth inspection"
    assert progress[-1]["depth"] == 2
    assert progress[-1]["model"] == "qwen3"
    assert progress[-1]["specialist_key"] == "code_research"
    assert progress[-1]["max_iter"] == 40
    snap = reg.snapshot(record.task_id)
    assert snap["title"] == "Auth inspection"
    assert len(snap["actions"]) == 1
    assert snap["actions"][0]["seq"] == 1
    assert snap["actions"][0]["tool"] == "read_file"
    assert snap["actions"][0]["detail"] == "auth.py"
    assert snap["actions"][0]["status"] == "done"


def test_snapshot_carries_live_context_fields():
    """update_child_live writes context_pct/iter/max_iter/tokens_in under the
    lock; snapshot() surfaces them so the GUI context bar + trace render."""
    reg = SubagentRegistry()
    lc = SubagentLifecycleManager(thresholds={"enabled": False})
    record = reg.register(_fake_child(), task="t", depth=1, lifecycle=lc)
    reg.update_child_live(
        record.task_id, context_pct=42.5, iter=7, max_iter=60, tokens_in=12345
    )
    snap = reg.snapshot(record.task_id)
    assert snap["context_pct"] == 42.5
    assert snap["iter"] == 7
    assert snap["max_iter"] == 60
    assert snap["tokens_in"] == 12345


def test_cancel_emits_killed_end_event():
    """cancel() pushes a subagent_end with status=killed immediately so the
    GUI panel reflects the kill before the thread finalizes."""
    reg, events = _make_registry_with_publish()
    lc = SubagentLifecycleManager(thresholds={"enabled": False})

    # A child that stays "running" so cancel has something to act on.
    class _HungChild:
        _subagent_cancelled = False
        _subagent_kill_reason = None
        session_manager = SimpleNamespace(history=[])

        def send_message(self, task):
            time.sleep(0.3)
            return {"status": "killed", "assistant_text": "", "tool_calls": []}

    record = reg.register(_HungChild(), task="t", depth=1, lifecycle=lc)
    reg.launch(record, "t")
    # Give the thread a moment to enter send_message.
    time.sleep(0.05)
    reg.cancel(record.task_id, grace_seconds=2.0)
    ends = [e for e in events if e.get("kind") == "subagent_end" and e.get("status") == "killed"]
    assert ends, f"expected a killed end event, events={events}"
    assert ends[0]["task_id"] == record.task_id


# ----------------------------------------------------------- blocking wait


def _slow_child(sleep_s: float, status: str = "completed", text: str = "done"):
    """A fake child that blocks in send_message for `sleep_s` then returns
    the given terminal status. Respects `_subagent_cancelled` so cancel()
    can wake it promptly (a real child exits at the next iteration
    boundary). Used to exercise registry.wait blocking."""

    class _Child:
        _subagent_cancelled = False
        _subagent_kill_reason = None
        session_manager = SimpleNamespace(history=[])

        def send_message(self, task):
            deadline = time.monotonic() + sleep_s
            while time.monotonic() < deadline:
                if getattr(self, "_subagent_cancelled", False):
                    return {
                        "status": "killed",
                        "assistant_text": "",
                        "tool_calls": [],
                        "tokens": {},
                    }
                time.sleep(0.02)
            return {
                "status": status,
                "assistant_text": text,
                "tool_calls": [{"name": "x"}],
                "tokens": {},
            }

    return _Child()


def _register_running(reg, child, task="t"):
    lc = SubagentLifecycleManager(thresholds={"enabled": False})
    return reg.register(child, task=task, depth=1, lifecycle=lc)


def test_registry_wait_returns_immediately_for_missing():
    """wait() on an unknown task_id returns the missing snapshot without
    blocking (no thread to wait on)."""
    reg = SubagentRegistry()
    snap = reg.wait("sa-doesnotexist", timeout=5.0)
    assert snap["status"] == "missing"


def test_registry_wait_returns_immediately_for_terminal():
    """wait() on a child already in a terminal state returns at once — the
    done_event was set when _run_child finalized it."""
    reg = SubagentRegistry()
    record = _register_running(reg, _slow_child(0.0))
    reg.launch(record, "t")
    # Poll the registry directly until the child finalizes.
    snap = None
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        snap = reg.snapshot(record.task_id)
        if snap["status"] != "running":
            break
        time.sleep(0.02)
    assert snap["status"] == "done"
    # Now wait must return immediately (no block) with the done snapshot.
    t0 = time.monotonic()
    snap2 = reg.wait(record.task_id, timeout=10.0)
    assert time.monotonic() - t0 < 0.5, "wait should not block on a terminal child"
    assert snap2["status"] == "done"


def test_registry_wait_blocks_until_child_finishes():
    """wait() blocks and returns the done snapshot once the child's run
    thread finalizes (~0.3s)."""
    reg = SubagentRegistry()
    record = _register_running(reg, _slow_child(0.3))
    reg.launch(record, "t")
    t0 = time.monotonic()
    snap = reg.wait(record.task_id, timeout=5.0)
    elapsed = time.monotonic() - t0
    assert snap["status"] == "done"
    # Blocked at least ~0.3s (waited for the child) but well under the timeout.
    assert 0.2 < elapsed < 4.5, f"unexpected wait elapsed={elapsed}"


def test_registry_wait_times_out_returns_running_snapshot():
    """wait(timeout=short) on a still-running child returns promptly with a
    running snapshot (the timer condition fired first)."""
    reg = SubagentRegistry()
    record = _register_running(reg, _slow_child(1.5))
    reg.launch(record, "t")
    # Let the thread enter send_message.
    time.sleep(0.05)
    t0 = time.monotonic()
    snap = reg.wait(record.task_id, timeout=0.1)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, f"wait should return ~0.1s, got {elapsed}"
    assert snap["status"] == "running"
    # Clean up the lingering child.
    reg.cancel(record.task_id, grace_seconds=2.0)


def test_cancel_wakes_concurrent_wait():
    """A wait() blocked on a long-running child wakes promptly when cancel()
    sets the done_event — it does not wait out its own timeout."""
    reg = SubagentRegistry()
    record = _register_running(reg, _slow_child(5.0))
    reg.launch(record, "t")
    time.sleep(0.05)

    result = {}

    def _waiter():
        result["snap"] = reg.wait(record.task_id, timeout=10.0)
        result["elapsed"] = time.monotonic() - result["start"]

    result["start"] = time.monotonic()
    th = threading.Thread(target=_waiter, daemon=True)
    th.start()
    # Cancel from the main thread; wait() should wake on the event.
    time.sleep(0.1)
    reg.cancel(record.task_id, grace_seconds=2.0)
    th.join(timeout=3.0)
    assert not th.is_alive(), "wait() did not wake on cancel"
    assert result["snap"]["status"] in ("killed", "done")
    assert result["elapsed"] < 2.5, f"wait did not wake promptly, elapsed={result['elapsed']}"


# ----------------------------------------------------------- await_subagent tool


def test_await_subagent_returns_done_immediately(tmp_path, monkeypatch):
    """await_subagent on a finished child returns the done snapshot without
    blocking (terminal short-circuit)."""
    target = tmp_path / "x.txt"
    target.write_text("p")
    provider = _ScriptedProvider(
        responses=[
            ProviderResponse(
                text="finished",
                parts=[MessagePart(type="text", text="finished")],
            )
        ]
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    res = execute("spawn_agent", {"task": "done fast"}, _ctx_for(parent))
    task_id = res["data"]["task_id"]
    snap = _poll_until(parent, task_id, timeout=5.0)
    assert snap["status"] == "done"

    t0 = time.monotonic()
    out = execute("await_subagent", {"task_id": task_id, "timeout": 10}, _ctx_for(parent))
    assert time.monotonic() - t0 < 0.5
    assert out["ok"] is True
    assert out["data"]["status"] == "done"


def test_await_subagent_times_out_returns_running(tmp_path, monkeypatch):
    """await_subagent with a short timeout on a still-running child returns
    error_code=timeout and the running snapshot."""
    target = tmp_path / "slow.txt"
    target.write_text("p")
    provider = _ScriptedProvider(
        responses=[_read_file_call(target) for _ in range(6)]
        + [ProviderResponse(text="end", parts=[MessagePart(type="text", text="end")])],
        delay=0.3,
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    res = execute("spawn_agent", {"task": "slow run"}, _ctx_for(parent))
    task_id = res["data"]["task_id"]

    t0 = time.monotonic()
    out = execute("await_subagent", {"task_id": task_id, "timeout": 0.1}, _ctx_for(parent))
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, f"await should wake ~0.1s, got {elapsed}"
    assert out["ok"] is False
    assert out["error_code"] == "timeout"
    assert out["data"]["status"] == "running"
    parent._subagent_registry.cancel(task_id, grace_seconds=2.0)


def test_await_subagent_blocks_then_returns_done(tmp_path, monkeypatch):
    """await_subagent blocks until the child finishes (within the timeout)
    and returns the done snapshot — the no-poll wait path."""
    target = tmp_path / "y.txt"
    target.write_text("p")
    provider = _ScriptedProvider(
        responses=[
            _read_file_call(target),
            ProviderResponse(text="ok", parts=[MessagePart(type="text", text="ok")]),
        ],
        delay=0.2,
    )
    parent = _build_parent(tmp_path, provider, monkeypatch)
    res = execute("spawn_agent", {"task": "two-step"}, _ctx_for(parent))
    task_id = res["data"]["task_id"]

    t0 = time.monotonic()
    out = execute("await_subagent", {"task_id": task_id, "timeout": 10}, _ctx_for(parent))
    elapsed = time.monotonic() - t0
    assert out["ok"] is True
    assert out["data"]["status"] == "done"
    assert elapsed < 5.0


def test_await_subagent_missing_task_id_and_no_registry(tmp_path, monkeypatch):
    """await_subagent validates task_id and registry presence like poll."""
    parent = _build_parent(tmp_path, _ScriptedProvider(responses=[]), monkeypatch)

    out = execute("await_subagent", {"task_id": ""}, _ctx_for(parent))
    assert out["ok"] is False and out["error_code"] == "invalid_args"

    out = execute("await_subagent", {"task_id": "sa-nope"}, _ctx_for(parent))
    # No registry on a fresh parent that never spawned -> no_registry.
    assert out["error_code"] in ("no_registry", "not_found")


def test_await_subagent_timeout_clamp(tmp_path, monkeypatch):
    """A huge timeout is clamped to MAX_TIMEOUT (no error), and a garbage
    timeout falls back to the default rather than blocking forever."""
    from mu.tools.agent.wait import _clamp_timeout, MAX_TIMEOUT, DEFAULT_TIMEOUT

    assert _clamp_timeout(10_000) == MAX_TIMEOUT
    assert _clamp_timeout("abc") == DEFAULT_TIMEOUT
    assert _clamp_timeout(-5) == DEFAULT_TIMEOUT
    assert _clamp_timeout(0) == 0
    assert _clamp_timeout(None) is None


# ----------------------------------------------------------- loop-detection exemption


def test_await_subagent_exempt_from_loop_detection():
    """await_subagent is in _BOOKKEEPING_TOOLS so repeated awaits (after
    timeouts) never trip the repeated-sequence detector. poll_subagent
    stays tracked so a busy-poll loop is still caught."""
    from mu.agent.loop_detection import (
        _BOOKKEEPING_TOOLS,
        track_tool_for_loop_detection,
        is_repeated_tool_sequence,
    )

    assert "await_subagent" in _BOOKKEEPING_TOOLS
    assert track_tool_for_loop_detection("await_subagent") is False
    assert track_tool_for_loop_detection("poll_subagent") is True

    # Because await_subagent is exempt, the loop body never adds it to the
    # fingerprint history — so even a synthetic all-await history would be
    # empty. Simulate the tracker's filter to prove the exemption holds:
    calls = [("await_subagent", {"task_id": "sa-1"})] * 8
    history = []
    for name, args in calls:
        if track_tool_for_loop_detection(name):
            history.append(f"{name}:x")
    assert history == [], f"exempt calls must not enter history, got {history}"
    assert is_repeated_tool_sequence(history) is False
