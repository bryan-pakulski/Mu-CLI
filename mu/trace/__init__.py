"""Run tracing for the agent loop.

A per-run JSONL trace is written to ``$MUCLI_HOME/trace/<session>_run_<id>.jsonl``
by :class:`TraceEmitter`. The emitter is hooked into ``mu/agent/loop_body.py``
(one record per iteration, plus standalone tool / nudge / compaction lines) and
flushed at turn end from ``mu/session/session.py``.

Everything here is defensive: a trace failure must NEVER break the agent loop.
``get_emitter`` returns ``None`` when tracing is disabled or the home dir is
unwritable, and every emit path is wrapped so a write error is swallowed.

The read side — :func:`parse_trace`, :func:`build_series`,
:func:`build_summary`, :func:`build_trace_snapshot`, plus the discovery
helpers :func:`list_trace_runs` / :func:`find_trace_path` — ingests a trace
file into dashboard-ready shapes, shared by the GUI router, the agent-facing
trace tools, and the ``mucli trace`` CLI.
"""

from __future__ import annotations

from .emitter import TraceEmitter, get_emitter, trace_dir, new_run_id
from .parser import (
    TraceRun,
    build_series,
    build_session_view,
    build_summary,
    combine_runs,
    find_trace_path,
    list_trace_runs,
    load_session_runs,
    parse_trace,
)
from .snapshot import build_trace_snapshot

__all__ = [
    "TraceEmitter",
    "get_emitter",
    "trace_dir",
    "new_run_id",
    "TraceRun",
    "parse_trace",
    "build_series",
    "build_summary",
    "build_trace_snapshot",
    "list_trace_runs",
    "find_trace_path",
    "load_session_runs",
    "combine_runs",
    "build_session_view",
]