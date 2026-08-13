"""Mode workspace contracts expose evidence without inventing certainty."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

from mu.gui.mode_workspace import (
    debug_workspace,
    feature_workspace,
    loop_workspace,
    research_workspace,
    security_workspace,
    teacher_workspace,
)
from mu.gui.routers.loop import LoopControlBody, control_loop
from mu.gui.routers.research import _memory_entry_to_dict


def _metric(workspace, metric_id):
    return next(item for item in workspace["metrics"] if item["id"] == metric_id)


def _quality(workspace, quality_id):
    return next(item for item in workspace["quality"] if item["id"] == quality_id)


def test_research_separates_source_credibility_from_claim_accuracy():
    workspace = research_workspace(
        [
            {"source_type": "paper", "credibility_score": 0.91},
            {"source_type": "blog", "credibility_score": 0.55},
        ],
        [
            {"content": "linked", "source": "paper-1", "record_type": "claim"},
            {"content": "unlinked", "source": "", "record_type": "claim"},
            {"content": "generic", "source": "", "record_type": "legacy_note"},
        ],
    )

    assert _metric(workspace, "credible")["value"] == 1
    assert _metric(workspace, "sourced")["value"] == 1
    assert _metric(workspace, "claims")["value"] == 2
    assert _quality(workspace, "accuracy")["state"] == "unassessed"
    assert "source" in _quality(workspace, "evidence")["detail"].lower()


def test_research_adapter_does_not_relabel_generic_memory_as_a_claim():
    base = {
        "id": 1,
        "content": "a note",
        "source": "",
        "kind": "finding",
        "created_at": 1.0,
        "updated_at": 2.0,
        "hits": 0,
    }
    legacy = _memory_entry_to_dict(SimpleNamespace(**base, tags=["project"]))
    claim = _memory_entry_to_dict(
        SimpleNamespace(**{**base, "source": "citation-1"}, tags=["research", "claim"])
    )

    assert legacy["record_type"] == "legacy_note"
    assert legacy["evidence_state"] == "evidence_gap"
    assert claim["record_type"] == "claim"
    assert claim["evidence_state"] == "source_linked"


def test_security_counts_each_verification_gate_independently():
    findings = [
        {
            "severity": "critical",
            "status": "approved",
            "proof_verified": True,
            "remediation_verified": True,
        },
        {
            "severity": "high",
            "status": "proof_attached",
            "proof_verified": False,
            "remediation_verified": False,
        },
    ]
    workspace = security_workspace({"title": "API audit", "status": "active"}, findings)

    assert workspace["title"] == "API audit"
    assert _metric(workspace, "risk")["value"] == 2
    assert _metric(workspace, "proof")["value"] == 1
    assert _metric(workspace, "fixes")["value"] == 1
    assert _quality(workspace, "accuracy")["state"] == "verification-gated"


def test_debug_keeps_investigation_state_distinct_from_accuracy():
    workspace = debug_workspace(
        "flaky login",
        [
            {"status": "untested"},
            {"status": "supported"},
            {"status": "disproved"},
        ],
        [{"content": "auth.py"}],
        [{"content": "fails under load"}],
        [],
    )

    assert _metric(workspace, "untested")["value"] == 1
    assert _metric(workspace, "supported")["value"] == 1
    assert _metric(workspace, "disproved")["value"] == 1
    assert _quality(workspace, "accuracy")["state"] == "unassessed"


def test_loop_reports_status_but_does_not_call_it_correctness():
    workspace = loop_workspace(
        "ship the release",
        [
            {"status": "completed"},
            {"status": "in_progress"},
            {"status": "blocked"},
        ],
        [{"id": "release-ui"}],
        [],
        loop_active=True,
    )

    assert workspace["status"]["label"] == "running"
    assert _metric(workspace, "completed")["value"] == 1
    assert _metric(workspace, "blocked")["value"] == 1
    assert _quality(workspace, "accuracy")["state"] == "unassessed"


def test_feature_uses_verified_exit_criteria_as_acceptance_evidence():
    plan = {
        "feature_name": "Mode OS",
        "feature_request": "Expose mode evidence",
        "overall_status": "in_progress",
        "phase_columns": [
            {
                "tasks": [
                    {
                        "status": "completed",
                        "exit_criteria": ["API typed", "UI renders"],
                        "verified_exit_criteria": ["API typed"],
                    },
                    {
                        "status": "blocked",
                        "exit_criteria": ["mobile tested"],
                        "verified_exit_criteria": [],
                    },
                ]
            }
        ],
        "review_records": [{"id": "review-1"}],
    }
    workspace = feature_workspace(plan, [{"feature_id": "mode-os"}])

    assert _metric(workspace, "progress")["value"] == "1/2"
    assert _metric(workspace, "criteria")["value"] == "1/3"
    assert _metric(workspace, "blocked")["value"] == 1
    assert _quality(workspace, "evidence")["state"] == "measured"


def test_teacher_average_only_uses_recorded_grades():
    course = {
        "subject": "Systems",
        "status": "in_progress",
        "lessons": [
            {"status": "completed"},
            {"status": "presenting"},
        ],
        "assignments": [
            {"grade": {"score_pct": 80}},
            {"grade": {"score_pct": 100}},
            {"grade": None},
        ],
        "scheduled_reviews": [{}],
    }
    workspace = teacher_workspace(course, [{"course_id": "systems"}])

    assert _metric(workspace, "lessons")["value"] == "1/2"
    assert _metric(workspace, "score")["value"] == "90%"
    assert _quality(workspace, "accuracy")["state"] == "measured-where-tested"


def test_each_mode_exposes_unique_lenses_with_a_shared_versioned_contract():
    workspaces = [
        research_workspace([], []),
        security_workspace(None, []),
        debug_workspace("", [], [], [], []),
        loop_workspace("", [], [], [], loop_active=False),
        feature_workspace(None, []),
        teacher_workspace(None, []),
    ]

    view_sets = []
    for workspace in workspaces:
        assert workspace["schema_version"] == 1
        assert workspace["objective"]
        assert workspace["provenance"]
        assert len(workspace["quality"]) == 3
        view_sets.append(tuple(view["id"] for view in workspace["views"]))

    assert len(set(view_sets)) == len(workspaces)


def test_loop_pause_resume_control_preserves_existing_workstreams():
    saves = []
    persisted_goals = []
    session = SimpleNamespace(
        variables={
            "loop_goal": "ship it",
            "loop_active": False,
            "loop_features": '[{"id":"existing"}]',
        },
        folder_context=None,
        session_manager=SimpleNamespace(
            save_history=lambda folder_context: saves.append(folder_context)
        ),
        _ensure_loop_goal_persistence=lambda: persisted_goals.append(True),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                session_by_name=lambda: session,
                session_lock_for=lambda: nullcontext(),
            )
        )
    )

    result = asyncio.run(control_loop(request, LoopControlBody(active=True, goal="")))

    assert result["loop_active"] is True
    assert session.variables["agent_mode"] == "loop"
    assert session.variables["loop_features"] == '[{"id":"existing"}]'
    assert persisted_goals == [True]
    assert saves == [None]
