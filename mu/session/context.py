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
from typing import Any, Optional


# Depth cap for sub-agent spawning (mirrors mu/tools/agent/spawn.py).
_MAX_SUBAGENT_DEPTH = 2


def _build_role_layer(role: str, session: Any) -> str:
    """LAYER 3B — Agent Role guidance. Kept under 500 chars.

    * ``parent``  — orchestrator instructions: delegate, don't block, poll,
      kill/extend, synthesize. Rendered only after the session has spawned
      at least one child (lazy gating via ``session_role``).
    * ``child``   — focused sub-agent instructions with depth + depth-cap
      message. Rendered for spawned sub-agent sessions.
    """
    role = (role or "").strip().lower()
    if role == "parent":
        # Count currently-registered children for context.
        n_active = 0
        try:
            n_active = sum(
                1 for r in session._subagent_registry.list() if r.status == "running"
            )
        except Exception:
            n_active = 0
        children = f"{n_active} child sub-agent(s) running" if n_active else "child sub-agents may be running"
        return (
            "You are the ORCHESTRATOR. You may spawn sub-agents for research, deep dives, and focused tasks.\n"
            f"- {children}. To wait for a child without burning iterations, call "
            "await_subagent(task_id, timeout=N) — it blocks your loop on a single "
            "tool call and wakes when the child finishes OR the timer fires, so no "
            "poll-loop warning triggers. Do NOT busy-poll poll_subagent in a tight "
            "loop; use poll_subagent only for a quick non-blocking check when you "
            "have other work to do meanwhile.\n"
            "- Use await_subagent(task_id, timeout=...) to block until results are ready; "
            "poll_subagent(task_id) for a quick non-blocking status check; "
            "kill_subagent(task_id) to cancel a stuck or unneeded child.\n"
            "- Sub-agents return summaries via await/poll. You synthesize their findings into the final response.\n"
            "- You can extend a child that needs more time (re-await with a longer timeout) or kill one that is looping."
        )
    if role == "child":
        try:
            depth = int(session.variables.get("subagent_depth", 1) or 1)
        except Exception:
            depth = 1
        remaining = max(0, _MAX_SUBAGENT_DEPTH - depth)
        if remaining <= 0:
            cap_line = "Do NOT spawn further sub-agents (depth cap reached)."
        else:
            cap_line = f"You may spawn up to {remaining} further sub-agent level(s)."
        return (
            f"You are a SUB-AGENT (depth={depth}), spawned by the parent orchestrator.\n"
            "- Complete your assigned task. Return a concise summary of findings via your final response.\n"
            f"- {cap_line}\n"
            "- Do NOT interact with the user. Return results to the parent only.\n"
            "- If you cannot complete the task, return what you have — partial results are valuable."
        )
    return ""


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


def inject_hierarchical_context(
    session: Any,
    system_prompt: str,
    *,
    cached_workspace: Optional[str] = None,
    cached_skills: Optional[str] = None,
    cached_folder_context: Optional[str] = None,
) -> str:
    """Compose the full layered system prompt sent to the provider.

    Layer order (each is omitted when empty):
      L0  Time prelude (current date/time)
      L1  Workspace context files (user-curated)
      L1B Installed skills (compact index or full bodies)
      L2  Conversation summary
      L3  Active task plan / current goal
      L5  Current-turn marker (telling the model to prioritize the
          live user message + current-turn tool results)

    ``cached_workspace`` / ``cached_skills`` let the caller reuse L1 / L1B
    text built once per turn (those read files from disk and are expensive
    to rebuild every iteration). When omitted (``None``), the layers are
    rebuilt from session as before. ``cached_folder_context`` does the same
    for L1C (workspace file tree + diffs), which is rebuilt per turn and
    refreshed mid-turn only when files change. L2 and L3 are always rebuilt fresh from
    in-memory state so mid-turn updates (auto-compaction rewriting the
    summary, tools updating feature_state / scratchpad) reach the model
    the same turn — the frozen-at-turn-start bug that caused long-horizon
    amnesia.
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

    workspace_files = (
        cached_workspace
        if cached_workspace is not None
        else build_workspace_context_files(session)
    )
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

    # LAYER 1C — Workspace file tree (paths only). Previously appended raw
    # to the system-prompt base (L0), where per-file change diffs grew
    # unbounded in long-horizon runs (~787k L0 bloat) and hid from layer
    # accounting. Now a tree-only layer (no diffs) bounded by
    # folder_context_max_chars, so the compactor and the /memory table see
    # it. The model reads file contents on demand via read_file/get_chunk.
    # Cached per turn; refreshed mid-turn only on file add/remove.
    folder_context_block = (
        cached_folder_context
        if cached_folder_context is not None
        else session._build_folder_context_block()
    )
    if folder_context_block:
        fc_limit = max(
            0,
            int(
                session.variables.get("folder_context_max_chars", 8192)
                or 8192
            ),
        )
        layers.append(
            "LAYER 1C — Workspace file tree:\n"
            f"[budget: {fc_limit} chars | tree-only, no diffs]\n"
            + folder_context_block
        )

    skills_block = (
        cached_skills
        if cached_skills is not None
        else session._build_skills_block(announce=True)
    )
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
    # LAYER 3B — Agent Role (parent orchestrator / child sub-agent). Skipped
    # when `session_role` is unset (single-agent sessions) so the prompt is
    # unchanged for the common case. Lazy: the parent is stamped "parent"
    # only on its first spawn; children are stamped "child" at creation.
    session_role = str(session.variables.get("session_role", "") or "").strip()
    if session_role:
        role_block = _build_role_layer(session_role, session)
        if role_block:
            layers.append("LAYER 3B — Agent role:\n" + role_block)
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
