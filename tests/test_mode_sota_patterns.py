"""Pins the SOTA patterns we expect every agentic mode to surface.

These tests guard against regressions where future edits silently drop
references to the frontier features the harness implements (parallel
tool execution, `spawn_agent`, `todo_*`, semantic retrieval, memory
discipline, etc.).

Each mode has a different appropriate emphasis — debug doesn't need
parallel-research delegation, feature doesn't need a user-visible todo
list (feature mode has its own task engine). The matrix below pins the
expected coverage per mode.
"""

import pytest

from utils.config import AGENTIC_MODES, AGENTIC_MODES, AGENTIC_SYSTEM_BASE


# ============================================================ shared base


def test_agentic_system_base_surfaces_full_tool_taxonomy():
    """The shared base prompt must enumerate every major tool family so the
    model knows the harness's capabilities regardless of which mode it's in."""
    base = AGENTIC_SYSTEM_BASE.lower()
    for needed in (
        "bash",  # shell catch-all (subsumes git, make, grep)
        "read_file",
        "apply_diff",
        "search_for_string",
        "retrieve_relevant_context",  # semantic index
        "spawn_agent",
        "todo_write",
        "save_memory",
        "save_scratchpad",
        "flush",
        "plan mode",
    ):
        assert needed in base, f"AGENTIC_SYSTEM_BASE missing reference to {needed!r}"


def test_agentic_system_base_documents_parallel_execution():
    """The model must know multiple tool calls in one turn run concurrently."""
    base = AGENTIC_SYSTEM_BASE.lower()
    assert "parallel" in base or "concurrent" in base


# ============================================================ default mode


def test_default_mode_leads_with_recall_and_semantic_retrieval():
    p = AGENTIC_MODES["default"].lower()
    assert "search_memory" in p, "default mode should prompt recall-first"
    assert "retrieve_relevant_context" in p, (
        "default mode should prefer semantic retrieval before manual reads"
    )


def test_default_mode_mentions_verification_via_bash():
    p = AGENTIC_MODES["default"].lower()
    assert "bash" in p
    assert "verif" in p, "default mode should require verifying changes"


def test_default_mode_mentions_todo_for_user_visibility():
    p = AGENTIC_MODES["default"]
    assert "todo_write" in p


def test_default_mode_mentions_parallel_dispatch():
    p = AGENTIC_MODES["default"].lower()
    assert "parallel" in p or "concurrent" in p


def test_default_mode_mentions_spawn_agent():
    p = AGENTIC_MODES["default"]
    assert "spawn_agent" in p


def test_default_mode_mentions_memory_save_for_durable_findings():
    p = AGENTIC_MODES["default"]
    assert "save_memory" in p


# ============================================================ debug mode


def test_debug_mode_leads_with_recall():
    p = AGENTIC_MODES["debug"]
    assert "search_memory" in p, "debug mode should check memory first"


def test_debug_mode_repro_first_then_locate_then_fix():
    p = AGENTIC_MODES["debug"].lower()
    # We can't pin exact step ordering but the keywords should all be present.
    for kw in ("reproduce", "locate", "verify"):
        assert kw in p, f"debug mode missing {kw!r}"


def test_debug_mode_mentions_parallel_inspection():
    p = AGENTIC_MODES["debug"].lower()
    assert "parallel" in p


def test_debug_mode_mentions_bisection_for_hard_bugs():
    p = AGENTIC_MODES["debug"].lower()
    assert "bisect" in p


def test_debug_mode_saves_root_cause_to_memory():
    p = AGENTIC_MODES["debug"]
    assert "save_memory" in p, "debug mode should persist root cause for next time"


def test_debug_mode_runs_full_suite_after_fix():
    p = AGENTIC_MODES["debug"].lower()
    # "whole" / "wider" / "full" — any of these signals broader verification.
    assert any(token in p for token in ("whole test", "wider", "full test", "race"))


# ============================================================ feature mode


def test_feature_mode_still_anchors_to_task_engine():
    """Feature mode must still mandate the canonical task engine — this is
    a hard rule that gates other behaviors."""
    p = AGENTIC_MODES["feature"]
    for tool in (
        "create_feature_task",
        "get_current_task",
        "get_tasks",
        "update_task_status",
        "approve_feature_task",
    ):
        assert tool in p, f"feature mode missing {tool!r}"


def test_feature_mode_mentions_within_phase_parallel_reads():
    p = AGENTIC_MODES["feature"].lower()
    assert "parallel" in p


def test_feature_mode_mentions_spawn_agent_for_research_phases():
    p = AGENTIC_MODES["feature"]
    assert "spawn_agent" in p


def test_feature_mode_requires_memory_and_scratchpad():
    p = AGENTIC_MODES["feature"]
    assert "save_memory" in p
    assert "save_scratchpad" in p


def test_feature_mode_requires_blocker_on_missing_input():
    p = AGENTIC_MODES["feature"]
    assert "raise_blocker" in p


# ============================================================ research mode


def test_research_mode_leads_with_recall():
    p = AGENTIC_MODES["research"]
    assert "search_memory" in p


def test_research_mode_codebase_uses_semantic_retrieval():
    p = AGENTIC_MODES["research"]
    assert "retrieve_relevant_context" in p


def test_research_mode_mentions_parallel_dispatch():
    p = AGENTIC_MODES["research"].lower()
    assert "parallel" in p


def test_research_mode_mentions_spawn_agent_for_deep_dives():
    p = AGENTIC_MODES["research"]
    assert "spawn_agent" in p


def test_research_mode_requires_citations():
    p = AGENTIC_MODES["research"].lower()
    for needed in ("citation", "credibility", "[^n]"):
        assert needed in p, f"research mode missing {needed!r}"


def test_research_mode_persists_findings_to_memory():
    p = AGENTIC_MODES["research"]
    assert "save_memory" in p


def test_research_mode_compact_form_preserves_workflow_section():
    """The short AGENTIC_MODES variant is what `/mode` sets;
    pin its structural shape."""
    p = AGENTIC_MODES["research"]
    assert "WORKFLOW" in p
    assert "credibility" in p.lower()


# ============================================================ loop mode


def test_loop_mode_uses_todo_for_visible_backlog():
    p = AGENTIC_MODES["loop"]
    assert "todo_write" in p
    assert "todo_set_status" in p


def test_loop_mode_mentions_parallel_context_gathering():
    p = AGENTIC_MODES["loop"].lower()
    assert "parallel" in p


def test_loop_mode_mentions_semantic_retrieval_for_reorient():
    p = AGENTIC_MODES["loop"]
    assert "retrieve_relevant_context" in p


def test_loop_mode_mentions_spawn_agent_for_side_quests():
    p = AGENTIC_MODES["loop"]
    assert "spawn_agent" in p


def test_loop_mode_requires_verification_evidence():
    p = AGENTIC_MODES["loop"].lower()
    assert "evidence" in p or "verify" in p


def test_loop_mode_requires_raise_blocker_over_silent_stall():
    p = AGENTIC_MODES["loop"]
    assert "raise_blocker" in p


def test_loop_mode_emphasizes_memory_compounding():
    p = AGENTIC_MODES["loop"].lower()
    assert "memory" in p
    assert "save_memory" in p or "save_scratchpad" in p


# ============================================================ all-modes consistency


@pytest.mark.parametrize("mode", ["default", "debug", "feature", "research", "loop"])
def test_every_mode_is_substantial(mode):
    """No mode should silently collapse to a one-liner."""
    p = AGENTIC_MODES[mode]
    assert len(p) > 400, f"mode {mode!r} prompt is suspiciously short ({len(p)} chars)"


@pytest.mark.parametrize("mode", ["default", "debug", "feature", "research", "loop"])
def test_every_mode_references_a_verification_strategy(mode):
    """A frontier-quality mode must tell the model how to verify, not just act."""
    p = AGENTIC_MODES[mode].lower()
    verification_signals = [
        "verify",
        "bash",
        "test",
        "evidence",
        "exit criteria",
        "cite",
        "bibliography",
        "review pass",
    ]
    found = [s for s in verification_signals if s in p]
    assert found, (
        f"mode {mode!r} has no verification strategy signal "
        f"(any of: {verification_signals})"
    )
