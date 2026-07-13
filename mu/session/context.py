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
    LAYER 3 (active goal) →
    LAYER 5 (current turn). Per-layer
    budgets + eviction policies are surfaced inline so they show up
    verbatim in `/memory list L*`.

These helpers delegate to other session methods that stay on the
`Session` class: `_build_active_goal_context`, `_build_skills_block`.
They also read `session.session_manager.conversation_summary`
and `session.session_manager.conversation_summary` for the L2 block.

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
        int(session.variables.get("workspace_context_max_chars", 16384) or 16384),
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
            session.variables.get("conversation_summary_char_limit", 24000)
            or 12000
        ),
    )
    summary = str(
        getattr(session.session_manager, "conversation_summary", "") or ""
    ).strip()
    if summary_limit and len(summary) > summary_limit:
        summary = summary[-summary_limit:].lstrip()

    # Prepend pinned session_goal to L2 summary as a durable preamble.
    # This ensures the goal survives compaction even if L3 is empty or
    # the session_goal variable is cleared at end of turn.
    session_goal = str(session.variables.get("session_goal", "") or "").strip()
    if session_goal and summary:
        summary = f"[Active Goal: {session_goal}]\n\n{summary}"

    goal_context = session._build_active_goal_context()
    # L4 (Recent tool activity) removed from system prompt — tool activity
    # now lives in messages: verbatim for recent calls, compressed with
    # [cache:KEY] tags for older calls (see prepare_runtime_history in
    # messages.py). The model can recall() cached results on demand.
    # This eliminates ~3000 tokens of redundant system-prompt content.

    layers: list[str] = []

    workspace_files = build_workspace_context_files(session)
    if workspace_files:
        ws_limit = max(
            0,
            int(
                session.variables.get("workspace_context_max_chars", 16384)
                or 8192
            ),
        )
        layers.append(
            "LAYER 1 — Workspace context files (user-curated, authoritative):\n"
            f"[budget: {ws_limit} chars | eviction: truncate-after-budget]\n"
            + workspace_files
        )

    skills_block = session._build_skills_block(announce=True)
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
    # L4B auto-retrieval removed — model uses retrieve_relevant_context
    # tool on demand instead of pre-injected snippets.

    layers.append(
        "LAYER 5 — Current turn:\n"
        "Always prioritize the live user message and current turn tool "
        "results over older context. "
        "Some older messages marked [PRESERVED CONTEXT] are kept verbatim "
        "and protected from summarisation — they are NOT stale or duplicated."
    )

    if not layers:
        return system_prompt
    return (
        f"{system_prompt}\n\n"
        "Hierarchical runtime context (layered with independent budgets/eviction):\n"
        + "\n\n".join(layers)
    )


__all__ = ["build_workspace_context_files", "inject_hierarchical_context"]
