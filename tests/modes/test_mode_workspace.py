"""Mode workspace contracts expose evidence without inventing certainty."""

from mu.gui.mode_workspace import research_workspace, security_workspace


def _metric(workspace, metric_id):
    return next(item for item in workspace["metrics"] if item["id"] == metric_id)


def _quality(workspace, quality_id):
    return next(item for item in workspace["quality"] if item["id"] == quality_id)


def test_research_grades_corroboration_without_calling_it_accuracy():
    workspace = research_workspace(
        [
            {"state": "cited", "origin": "nature.com", "assessed": True},
            {"state": "cited", "origin": "who.int", "assessed": False},
            {"state": "read", "origin": "nature.com", "assessed": False},
        ],
        [
            {"confidence": "high", "contested": False},
            {"confidence": "low", "contested": True},
        ],
        [
            {"id": "q1", "status": "answered", "claims": 1},
            {"id": "q2", "status": "contested", "claims": 1},
            {"id": "q3", "status": "open", "claims": 0},
            {"id": "q4", "status": "dropped", "claims": 0},
        ],
    )

    assert _metric(workspace, "high")["value"] == 1
    assert _metric(workspace, "contested")["value"] == 1
    assert _metric(workspace, "gaps")["value"] == 1  # the dropped question is not a gap
    assert _metric(workspace, "questions")["value"] == "1/3"
    assert "2 independent origin" in _metric(workspace, "sources")["detail"]
    assert _quality(workspace, "accuracy")["state"] == "unassessed"


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


def test_each_mode_exposes_unique_lenses_with_a_shared_versioned_contract():
    workspaces = [
        research_workspace([], []),
        security_workspace(None, []),
    ]

    view_sets = []
    for workspace in workspaces:
        assert workspace["schema_version"] == 1
        assert workspace["objective"]
        assert workspace["provenance"]
        assert len(workspace["quality"]) == 3
        view_sets.append(tuple(view["id"] for view in workspace["views"]))

    assert len(set(view_sets)) == len(workspaces)
