"""Live progress tracker for parallel sub-agent dispatch.

When the parent agent issues multiple `spawn_agent` calls in one turn,
per-call "🔨 Running tool: X" log lines flood the terminal —
each child can fire dozens of those during its run.

`SubagentProgressTracker` replaces that stream with a compact in-place
live panel that shows one row per active sub-agent:

    ┌─ Sub-agents (3 active, 1 done) ────────────────────────┐
    │ d │ task                  │ status              │ calls │ elapsed │
    │ 1 │ Review agent loop ar… │ 🔨 read_file        │   3   │  12.4s  │
    │ 1 │ Review available tools │ 🔨 search_for_str… │   5   │  12.4s  │
    │ 1 │ Review commands & UI  │ ✓ done — All 11…    │   4   │   8.1s  │
    │ 1 │ Review tests & core   │ 🔨 list_dir         │   2   │   9.2s  │
    └────────────────────────────────────────────────────────┘

Thread-safety: every state-changing method takes a lock. The render
method takes a snapshot so the Rich `Live` redraw never observes a
half-updated row.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class _AgentState:
    agent_id: str
    depth: int
    task: str
    started_at: float
    current_tool: Optional[str] = None
    tool_count: int = 0
    # "running" | "done" | "error" | "killed" | "stuck"
    status: str = "running"
    summary: str = ""
    finished_at: Optional[float] = None
    kill_reason: Optional[str] = None
    repeat_count: int = 0  # consecutive same-tool+args (stuck signal)
    # The registry task_id for this row, set after register() allocates it.
    # Lets per-tool / state-change emits carry the GUI panel's upsert key.
    task_id: Optional[str] = None


class SubagentProgressTracker:
    """Thread-safe state about sub-agents currently being dispatched."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: Dict[str, _AgentState] = {}
        self._order: List[str] = []
        self._next_id = 0
        # Optional GUI live-push callback, attached by SubagentRegistry when
        # the parent UI is a WebUI. When set, per-tool / state changes emit
        # ``subagent_progress`` events so the chat-feed panel updates live
        # without waiting for the parent's next provider call.
        self._publish: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------ mutation

    def open(self, *, depth: int, task: str) -> str:
        """Register a sub-agent that just started. Returns a unique id
        the caller passes back to `update_tool` / `close`."""
        with self._lock:
            self._next_id += 1
            agent_id = f"sa{self._next_id}"
            self._agents[agent_id] = _AgentState(
                agent_id=agent_id,
                depth=depth,
                task=task,
                started_at=time.monotonic(),
            )
            self._order.append(agent_id)
            return agent_id

    def update_tool(self, agent_id: str, tool_name: str) -> None:
        """Record that this sub-agent is now running `tool_name`."""
        with self._lock:
            state = self._agents.get(agent_id)
            if state is None or state.status not in ("running", "stuck"):
                return
            state.current_tool = tool_name
            state.tool_count += 1
        self._emit_progress(agent_id)

    def set_state(
        self,
        agent_id: str,
        *,
        stuck: bool = False,
        stall: bool = False,
        repeat_count: int = 0,
        status: Optional[str] = None,
        kill_reason: Optional[str] = None,
    ) -> None:
        """Push a lifecycle signal (stuck/stall/kill) into a row's state.

        Called by ``SubagentRegistry`` when the lifecycle manager detects a
        stuck/stall transition or when the parent kills a child, so the
        panel reflects it without waiting for the next render.
        """
        with self._lock:
            state = self._agents.get(agent_id)
            if state is None:
                return
            if status is not None:
                state.status = status
            elif stuck and state.status == "running":
                state.status = "stuck"
            elif not stuck and state.status == "stuck":
                # Stuck cleared (child moved on to a different tool).
                state.status = "running"
            state.repeat_count = int(repeat_count)
            if kill_reason is not None:
                state.kill_reason = kill_reason
        self._emit_progress(agent_id)

    def set_task_id(self, agent_id: str, task_id: str) -> None:
        """Stamp the registry task_id onto a pre-opened row so progress emits
        carry the GUI panel's upsert key."""
        with self._lock:
            state = self._agents.get(agent_id)
            if state is not None:
                state.task_id = task_id

    def _emit_progress(self, agent_id: str) -> None:
        """Push a ``subagent_progress`` event for one row to the GUI bus.

        No-op when no ``_publish`` is attached or the row has no linked
        ``task_id`` yet (the registry links it right after ``register``).
        Builds the payload from a locked snapshot so the emit never observes
        a half-updated row.
        """
        fn = self._publish
        if fn is None:
            return
        with self._lock:
            s = self._agents.get(agent_id)
            if s is None or s.task_id is None:
                return
            now = time.monotonic()
            end_time = s.finished_at if s.finished_at is not None else now
            payload = {
                "kind": "subagent_progress",
                "task_id": s.task_id,
                "tool_count": s.tool_count,
                "last_tool": s.current_tool,
                "status": s.status,
                "stuck": s.status == "stuck",
                "repeat_count": s.repeat_count,
                "elapsed": round(max(0.0, end_time - s.started_at), 2),
            }
        try:
            fn(payload)
        except Exception:  # noqa: BLE001
            pass

    def close(
        self,
        agent_id: str,
        *,
        tool_count: int,
        summary: str,
        error: Optional[str] = None,
        status: str = "done",
        kill_reason: Optional[str] = None,
    ) -> None:
        with self._lock:
            state = self._agents.get(agent_id)
            if state is None:
                return
            # Don't downgrade a kill recorded via set_state back to "done".
            if status == "done" and state.status == "killed":
                status = "killed"
            state.status = "error" if error else status
            state.tool_count = tool_count
            state.summary = str(error) if error else str(summary)
            state.current_tool = None
            state.kill_reason = kill_reason
            state.finished_at = time.monotonic()

    # --------------------------------------------------------- introspection

    def has_active(self) -> bool:
        with self._lock:
            return any(
                a.status in ("running", "stuck") for a in self._agents.values()
            )

    def snapshot(self) -> List[_AgentState]:
        """Deep-copy snapshot for read-only consumers (rendering)."""
        with self._lock:
            return [
                _AgentState(
                    agent_id=a.agent_id,
                    depth=a.depth,
                    task=a.task,
                    started_at=a.started_at,
                    current_tool=a.current_tool,
                    tool_count=a.tool_count,
                    status=a.status,
                    summary=a.summary,
                    finished_at=a.finished_at,
                    kill_reason=a.kill_reason,
                    repeat_count=a.repeat_count,
                    task_id=a.task_id,
                )
                for a in (self._agents[aid] for aid in self._order)
            ]

    # --------------------------------------------------------------- render

    def render_panel(self, *, max_task_chars: int = 40, max_status_chars: int = 50):
        """Return a Rich Table renderable for the current snapshot."""
        # Local imports — rich is a hard dep of the harness but we keep the
        # tracker importable in headless contexts that don't render anything.
        from rich.table import Table
        from rich.text import Text
        from rich import box

        snap = self.snapshot()
        running = sum(1 for s in snap if s.status in ("running", "stuck"))
        done = sum(1 for s in snap if s.status == "done")
        errored = sum(1 for s in snap if s.status == "error")
        killed = sum(1 for s in snap if s.status == "killed")

        title_parts = [f"{running} active"]
        if done:
            title_parts.append(f"{done} done")
        if errored:
            title_parts.append(f"{errored} errored")
        if killed:
            title_parts.append(f"{killed} killed")
        title = "Sub-agents (" + ", ".join(title_parts) + ")"

        table = Table(title=title, box=box.ROUNDED, expand=True, show_lines=False)
        table.add_column("depth", justify="center", width=5, style="cyan", no_wrap=True)
        table.add_column("task", overflow="ellipsis", no_wrap=True, max_width=max_task_chars)
        table.add_column(
            "status", overflow="ellipsis", no_wrap=True, max_width=max_status_chars
        )
        table.add_column("calls", justify="right", width=5)
        table.add_column("elapsed", justify="right", width=8)

        now = time.monotonic()

        # Parent orchestrator row — shows what the parent is doing while
        # children run. Rendered first so the user sees the orchestrator
        # context above its children.
        if any(s.status in ("running", "stuck") for s in snap):
            parent_status = Text(
                "orchestrating — waiting on N children".replace(
                    "N", str(running)
                ),
                style="bold cyan",
            )
            table.add_row("d=0", "(parent)", parent_status, "", "")

        for state in snap:
            task_text = state.task if len(state.task) <= max_task_chars else state.task[: max_task_chars - 1] + "…"
            end_time = state.finished_at if state.finished_at is not None else now
            elapsed_s = max(0.0, end_time - state.started_at)
            elapsed = f"{elapsed_s:.1f}s"

            if state.status == "running":
                tool = state.current_tool or "(starting…)"
                status_text = Text(f"🔨 {tool}", style="yellow")
            elif state.status == "stuck":
                tool = state.current_tool or "?"
                status_text = Text(
                    f"⚠ stuck — {state.repeat_count}x {tool}", style="bold yellow"
                )
            elif state.status == "killed":
                reason = state.kill_reason or "killed"
                summary = state.summary or reason
                if len(summary) > max_status_chars - 6:
                    summary = summary[: max_status_chars - 7] + "…"
                status_text = Text(f"⏹ killed ({reason})", style="magenta")
            elif state.status == "done":
                summary = state.summary
                if len(summary) > max_status_chars - 4:
                    summary = summary[: max_status_chars - 5] + "…"
                status_text = Text(f"✓ {summary}", style="green")
            else:
                summary = state.summary
                if len(summary) > max_status_chars - 4:
                    summary = summary[: max_status_chars - 5] + "…"
                status_text = Text(f"✗ {summary}", style="red")

            table.add_row(
                f"d={state.depth}",
                task_text,
                status_text,
                str(state.tool_count),
                elapsed,
            )

        return table

    # --------------------------------------------------- non-TUI fallback

    def emit_structured_event(self) -> str:
        """Return a one-line JSON snapshot of every sub-agent row.

        For headless / JSON mode (no Rich console) this is the progress
        surface: callers (``poll_subagent``, the parent loop) log it so
        sub-agent status is observable without a TUI panel. Safe to call
        when no agents are registered (returns an empty-events string).
        """
        import json as _json

        snap = self.snapshot()
        now = time.monotonic()
        rows = []
        for s in snap:
            end_time = s.finished_at if s.finished_at is not None else now
            rows.append(
                {
                    "agent_id": s.agent_id,
                    "depth": s.depth,
                    "task": s.task,
                    "status": s.status,
                    "tool": s.current_tool,
                    "tool_count": s.tool_count,
                    "elapsed": round(max(0.0, end_time - s.started_at), 2),
                    "kill_reason": s.kill_reason,
                    "repeat_count": s.repeat_count,
                    "summary": s.summary,
                }
            )
        return _json.dumps({"subagents": rows}, default=str)


__all__ = ["SubagentProgressTracker"]
