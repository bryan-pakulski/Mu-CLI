"""Trace-reading tools (agentic debugging).

Read-only `@tool` handlers that expose the per-run JSONL traces written to
``$MUCLI_HOME/trace/`` to the agent loop and spawned subagents, so an agent can
inspect a past or in-flight run — drift, compactions, nudges, redundant reads,
subagent stalls, per-iteration latency — and debug it. Thin wrappers over
``mu/trace/parser.py``; no writes.
"""

from . import handlers  # noqa: F401 — registers list/summary/series/iteration tools

__all__: list = []