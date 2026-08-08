"""Hierarchical context assembly for the system prompt.

L2 is now state-first: structured runtime state is projected deterministically
from tool envelopes/stores. The rolling conversation summary remains a bounded
semantic residue for information that cannot be derived structurally.
"""
from __future__ import annotations

import os
from typing import Any, Optional

_MAX_SUBAGENT_DEPTH = 2


def _build_role_layer(role: str, session: Any) -> str:
    """Minimal role metadata; detailed specialist policy lives in spawn.py."""
    role = (role or "").strip().lower()
    if role == "parent":
        try:
            n_active = sum(1 for r in session._subagent_registry.list() if r.status == "running")
        except Exception:
            n_active = 0
        return (
            f"ROLE: ORCHESTRATOR ({n_active} active delegation(s)). "
            "Persistent specialists push material findings/completions through the unread mailbox. "
            "Use await_subagent when you must block; poll_subagent only occasionally; "
            "kill_subagent cancels an unneeded or stuck delegation."
        )
    if role == "child":
        try:
            depth = int(session.variables.get("subagent_depth", 1) or 1)
        except Exception:
            depth = 1
        remaining = max(0, _MAX_SUBAGENT_DEPTH - depth)
        cap = "depth cap reached; do not spawn further sub-agents" if remaining <= 0 else f"you may spawn up to {remaining} further sub-agent level(s)"
        return f"ROLE: SUB-AGENT depth={depth}; persistent specialist policy is in the base system instruction; {cap}."
    return ""


def build_workspace_context_files(session: Any) -> str:
    folder_context = session.folder_context
    if not folder_context or not folder_context.folders:
        return ""
    raw_names = str(session.variables.get("workspace_context_files", "AGENTS.md,CLAUDE.md,MUCLI.md,.mu/CONTEXT.md") or "")
    candidates = [n.strip() for n in raw_names.split(",") if n.strip()]
    budget = max(0, int(session.variables.get("workspace_context_max_chars", 16384) or 16384))
    if not candidates or budget <= 0:
        return ""
    blocks: list[str] = []
    used = 0
    seen: set[str] = set()
    for folder in folder_context.folders:
        for name in candidates:
            path = os.path.normpath(os.path.join(folder, name))
            if path in seen:
                continue
            seen.add(path)
            if not os.path.isfile(path):
                continue
            try:
                body = open(path, "r", encoding="utf-8", errors="replace").read().strip()
            except OSError:
                continue
            if not body:
                continue
            entry = f"### {os.path.relpath(path, folder)}  (from {folder})\n{body}"
            remaining = budget - used
            if remaining <= 0:
                break
            if len(entry) > remaining:
                entry = entry[:remaining].rstrip() + "\n...[truncated]"
            blocks.append(entry)
            used += len(entry) + 2
            if used >= budget:
                break
        if used >= budget:
            break
    return "\n\n".join(blocks).strip()


def build_attachment_context(session: Any) -> str:
    registry = getattr(session, "attachment_registry", None)
    if registry is None:
        return ""
    try:
        items = registry.list()
    except Exception:
        return ""
    if not items:
        return ""
    lines = ["Uploaded documents are durable session inputs. Retrieve contents on demand; do not guess them."]
    for item in items[:30]:
        lines.append("- id={attachment_id} | {name} | {mime_type} | {size} bytes".format(
            attachment_id=item.get("attachment_id", ""), name=item.get("name", "attachment"),
            mime_type=item.get("mime_type", "application/octet-stream"), size=int(item.get("size", 0) or 0)))
    if len(items) > 30:
        lines.append(f"- ... {len(items)-30} more; call list_attachments")
    return "\n".join(lines)[:6000]


def inject_hierarchical_context(session: Any, system_prompt: str, *, cached_workspace: Optional[str] = None, cached_skills: Optional[str] = None, cached_folder_context: Optional[str] = None) -> str:
    try:
        from utils.runtime_metrics import _current_time_prelude
        system_prompt = f"{_current_time_prelude()}\n\n{system_prompt}".strip()
    except Exception:
        pass

    summary_limit = max(0, int(session.variables.get("conversation_summary_char_limit", 24000) or 12000))
    semantic_residue = str(getattr(session.session_manager, "conversation_summary", "") or "").strip()
    if summary_limit and len(semantic_residue) > summary_limit:
        semantic_residue = semantic_residue[-summary_limit:].lstrip()

    # Deterministic state refresh also replaces periodic LLM progress checkpoints
    # on this SessionManager. Do not duplicate session_goal here; L3 owns it.
    try:
        from mu.session.state_capsule import build_state_capsule
        state_capsule = build_state_capsule(session, max_chars=summary_limit or 12000, include_goal=False)
    except Exception:
        state_capsule = ""

    goal_context = session._build_active_goal_context()
    layers: list[str] = []

    workspace_files = cached_workspace if cached_workspace is not None else build_workspace_context_files(session)
    if workspace_files:
        limit = max(0, int(session.variables.get("workspace_context_max_chars", 16384) or 8192))
        layers.append(f"LAYER 1 — Workspace context files (user-curated, authoritative):\n[budget: {limit} chars | eviction: truncate-after-budget]\n{workspace_files}")

    session_type = str(session.variables.get("session_type", "workspace") or "workspace").lower()
    if session_type == "container":
        from mu.container.context import build_container_context
        folder_context_block = build_container_context(session)
    else:
        folder_context_block = cached_folder_context if cached_folder_context is not None else session._build_folder_context_block()
    if folder_context_block:
        limit = max(0, int(session.variables.get("folder_context_max_chars", 8192) or 8192))
        title = "LAYER 1C — Container sandbox:" if session_type == "container" else "LAYER 1C — Workspace file tree:"
        layers.append(f"{title}\n[budget: {limit} chars | tree-only, no diffs]\n{folder_context_block}")

    attachment_context = build_attachment_context(session)
    if attachment_context:
        layers.append("LAYER 1D — User-uploaded attachment registry (metadata only):\n[budget: 6000 chars | contents retrieved on demand]\n" + attachment_context)

    skills_block = cached_skills if cached_skills is not None else session._build_skills_block(announce=True)
    if skills_block:
        limit = max(0, int(session.variables.get("skills_max_chars", 6144) or 6144))
        layers.append(f"LAYER 1B — Installed skills (compact index; bodies auto-load on trigger or via `invoke_skill`):\n[budget: {limit} chars | eviction: drop-tail after auto-expand]\n{skills_block}")

    if state_capsule or semantic_residue:
        parts = [f"[budget: {summary_limit} chars | eviction: keep newest]"]
        if state_capsule:
            parts.append(state_capsule)
        if semantic_residue:
            parts.append("Semantic residue from compacted older conversation (non-authoritative where structured state disagrees):\n" + semantic_residue)
        layers.append("LAYER 2 — Conversation summary:\n" + "\n\n".join(parts))

    if goal_context:
        layers.append("LAYER 3 — Active task plan / current goal:\n" + goal_context)

    session_role = str(session.variables.get("session_role", "") or "").strip()
    if session_role:
        role_block = _build_role_layer(session_role, session)
        if role_block:
            layers.append("LAYER 3B — Agent role:\n" + role_block)

    layers.append("LAYER 5 — Current turn:\nAlways prioritize the live user message and current-turn tool results. Structured L2 state is authoritative; older semantic residue is fallback context only.")
    return f"{system_prompt}\n\nHierarchical runtime context (layered with independent budgets/eviction):\n" + "\n\n".join(layers)


__all__ = ["build_attachment_context", "build_workspace_context_files", "inject_hierarchical_context"]
