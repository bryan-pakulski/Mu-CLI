"""Hierarchical context assembly for the system prompt.

Two top-level helpers, both consumed by the agent loop just before
sending to the provider:

  * `build_workspace_context_files(session)` — LAYER 1: concat any
    user-curated `AGENTS.md`/`CLAUDE.md`/`MUCLI.md`/`.mu/CONTEXT.md`
    files from each attached workspace folder, with provenance headers
    and a `workspace_context_max_chars` budget.

  * `inject_hierarchical_context(session, system_prompt)` — assemble
    the full layered system prompt: time prelude → LAYER 1
    (workspace files) → LAYER 1B (skills) → LAYER 2 (summary) →
    LAYER 3 (active goal) → LAYER 4 (recent tool activity) → LAYER
    4B (retrieved snippets) → LAYER 5 (current turn). Per-layer
    budgets + eviction policies are surfaced inline so they show up
    verbatim in `/memory list L*`.

These helpers delegate to other session methods that stay on the
`Session` class: `_build_active_goal_context`, `_build_recent_tool_context`,
`_build_skills_block`. They also read `session.session_manager.conversation_summary`
and `session._pending_retrieved_context` for the L2 and L4B blocks.

Tests: `tests/test_workspace_context_files.py` (LAYER 1),
`tests/test_skills.py` (LAYER 1B injection),
`tests/test_time_awareness.py` (time prelude),
`tests/test_session.py` (layer ordering + budgets).
"""

from __future__ import annotations

import os
from typing import Any


def build_workspace_context_files(session: Any) -> str:
    """LAYER 1 — read any user-curated context files from the workspace
    folders and concatenate with provenance headers. Returns "" when
    no folders are attached, no files match, or the feature is
    disabled via `workspace_context_files = ""`.
    """
    folder_context = session.folder_context
    if not folder_context or not folder_context.folders:
        return ""
    raw_names = str(
        session.variables.get(
            "workspace_context_files", "AGENTS.md,CLAUDE.md,MUCLI.md,.mu/CONTEXT.md"
        )
        or ""
    )
    candidates = [n.strip() for n in raw_names.split(",") if n.strip()]
    if not candidates:
        return ""
    budget = max(
        0,
        int(session.variables.get("workspace_context_max_chars", 8192) or 8192),
    )
    if budget == 0:
        return ""
    blocks: list[str] = []
    used = 0
    seen_paths: set[str] = set()
    for folder in folder_context.folders:
        for name in candidates:
            path = os.path.normpath(os.path.join(folder, name))
            if path in seen_paths:
                continue
            seen_paths.add(path)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    body = fh.read().strip()
            except OSError:
                continue
            if not body:
                continue
            header = f"### {os.path.relpath(path, folder)}  (from {folder})"
            entry = f"{header}\n{body}"
            remaining = budget - used
            if remaining <= 0:
                break
            if len(entry) > remaining:
                entry = entry[:remaining].rstrip() + "\n...[truncated]"
            blocks.append(entry)
            used += len(entry) + 2  # account for separator
            if used >= budget:
                break
        if used >= budget:
            break
    return "\n\n".join(blocks).strip()


def inject_hierarchical_context(session: Any, system_prompt: str) -> str:
    """Compose the full layered system prompt sent to the provider.

    Layer order (each is omitted when empty):
      L0  Time prelude (current date/time)
      L1  Workspace context files (user-curated)
      L1B Installed skills (compact index or full bodies)
      L2  Conversation summary
      L3  Active task plan / current goal
      L4  Recent tool activity
      L4B Retrieved workspace snippets
      L5  Current-turn marker (telling the model to prioritize the
          live user message + current-turn tool results)
    """
    # L0 — prepend a time-awareness banner so the model isn't guessing
    # at the wall clock. Cheap (~25 tokens) and reflected in L0 of
    # the /memory table via compose_base_system_prompt.
    try:
        from utils.runtime_metrics import _current_time_prelude

        system_prompt = f"{_current_time_prelude()}\n\n{system_prompt}".strip()
    except Exception:
        pass

    summary_limit = max(
        0,
        int(
            session.variables.get("conversation_summary_char_limit", 8000)
            or 8000
        ),
    )
    summary = str(
        getattr(session.session_manager, "conversation_summary", "") or ""
    ).strip()
    if summary_limit and len(summary) > summary_limit:
        summary = summary[-summary_limit:].lstrip()

    goal_context = session._build_active_goal_context()
    tool_context = session._build_recent_tool_context(
        max_chars=max(
            0,
            int(
                session.variables.get("recent_tool_context_char_limit", 12000)
                or 12000
            ),
        )
    )

    layers: list[str] = []

    workspace_files = build_workspace_context_files(session)
    if workspace_files:
        ws_limit = max(
            0,
            int(
                session.variables.get("workspace_context_max_chars", 8192)
                or 8192
            ),
        )
        layers.append(
            "LAYER 1 — Workspace context files (user-curated, authoritative):\n"
            f"[budget: {ws_limit} chars | eviction: truncate-after-budget]\n"
            + workspace_files
        )

    skills_block = session._build_skills_block()
    if skills_block:
        sk_limit = max(
            0, int(session.variables.get("skills_max_chars", 6144) or 6144)
        )
        layers.append(
            "LAYER 1B — Installed skills (compact index; bodies auto-load on trigger or via `invoke_skill`):\n"
            f"[budget: {sk_limit} chars | eviction: drop-tail after auto-expand]\n"
            + skills_block
        )

    if summary:
        layers.append(
            "LAYER 2 — Conversation summary:\n"
            f"[budget: {summary_limit} chars | eviction: keep newest]\n{summary}"
        )

    if goal_context:
        layers.append(
            "LAYER 3 — Active task plan / current goal:\n" + goal_context
        )

    if tool_context:
        tool_limit = max(
            0,
            int(
                session.variables.get("recent_tool_context_char_limit", 12000)
                or 12000
            ),
        )
        layers.append(
            "LAYER 4 — Recent tool activity (latest first):\n"
            f"[budget: {tool_limit} chars | eviction: drop oldest tool records]\n"
            + tool_context
        )

    retrieved_context = str(
        getattr(session, "_pending_retrieved_context", "") or ""
    ).strip()
    if retrieved_context:
        retrieval_limit = max(
            1,
            int(
                session.variables.get("retrieval_context_char_limit", 5000)
                or 5000
            ),
        )
        if len(retrieved_context) > retrieval_limit:
            retrieved_context = retrieved_context[:retrieval_limit].rstrip()
        layers.append(
            "LAYER 4B — Retrieved workspace snippets:\n"
            f"[budget: {retrieval_limit} chars | eviction: drop lowest-ranked snippets]\n"
            + retrieved_context
        )

    layers.append(
        "LAYER 5 — Current turn:\n"
        "Always prioritize the live user message and current turn tool "
        "results over older context."
    )

    if not layers:
        return system_prompt
    return (
        f"{system_prompt}\n\n"
        "Hierarchical runtime context (layered with independent budgets/eviction):\n"
        + "\n\n".join(layers)
    )


__all__ = ["build_workspace_context_files", "inject_hierarchical_context"]
