"""Live progress tracker for parallel sub-agent dispatch.

When the parent agent issues multiple `spawn_agent` calls in one turn,
the legacy per-call "🔨 Running tool: X" log lines flood the terminal —
each child can fire dozens of those during its run.

`SubagentProgressTracker` replaces that stream with a compact in-place
live panel that shows one row per active sub-agent:

    ┌─ Sub-agents (3 active, 1 done) ────────────────────────┐
    │ d │ task                  │ status              │ calls │ elapsed │
    │ 1 │ Review agent loop ar… │ 🔨 read_file        │   3   │  12.4s  │
    │ 1 │ Review tools & MCP    │ 🔨 search_for_str…  │   5   │  12.4s  │
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
from typing import Dict, List, Optional


@dataclass
class _AgentState:
    agent_id: str
    depth: int
    task: str
    started_at: float
    current_tool: Optional[str] = None
    tool_count: int = 0
    status: str = "running"  # "running" | "done" | "error"
    summary: str = ""
    finished_at: Optional[float] = None


class SubagentProgressTracker:
    """Thread-safe state about sub-agents currently being dispatched."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: Dict[str, _AgentState] = {}
        self._order: List[str] = []
        self._next_id = 0

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
            if state is None or state.status != "running":
                return
            state.current_tool = tool_name
            state.tool_count += 1

    def close(
        self,
        agent_id: str,
        *,
        tool_count: int,
        summary: str,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            state = self._agents.get(agent_id)
            if state is None:
                return
            state.status = "error" if error else "done"
            state.tool_count = tool_count
            state.summary = str(error) if error else str(summary)
            state.current_tool = None
            state.finished_at = time.monotonic()

    # --------------------------------------------------------- introspection

    def has_active(self) -> bool:
        with self._lock:
            return any(a.status == "running" for a in self._agents.values())

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
        running = sum(1 for s in snap if s.status == "running")
        done = sum(1 for s in snap if s.status == "done")
        errored = sum(1 for s in snap if s.status == "error")

        title_parts = [f"{running} active"]
        if done:
            title_parts.append(f"{done} done")
        if errored:
            title_parts.append(f"{errored} errored")
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
        for state in snap:
            task_text = state.task if len(state.task) <= max_task_chars else state.task[: max_task_chars - 1] + "…"
            end_time = state.finished_at if state.finished_at is not None else now
            elapsed_s = max(0.0, end_time - state.started_at)
            elapsed = f"{elapsed_s:.1f}s"

            if state.status == "running":
                tool = state.current_tool or "(starting…)"
                status_text = Text(f"🔨 {tool}", style="yellow")
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


__all__ = ["SubagentProgressTracker"]
