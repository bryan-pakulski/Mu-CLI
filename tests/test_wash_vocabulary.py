"""The shared Wash vocabulary covers every job enum the UIs can display."""

from __future__ import annotations

from mu.design import load_vocabulary
from mu.jobs.core.review_report import VERDICTS
from mu.jobs.core.states import JobState
from mu.jobs.models import AttentionReason, JobStatus


def test_every_job_state_is_described():
    vocab = load_vocabulary()
    assert set(vocab["job_state"]) == {s.value for s in JobState}
    assert set(vocab["pipeline"]) <= set(vocab["job_state"])


def test_every_legacy_job_status_is_described():
    assert set(load_vocabulary()["job_status"]) == {s.value for s in JobStatus}


def test_every_attention_reason_is_described():
    assert set(load_vocabulary()["attention_reason"]) == {r.value for r in AttentionReason}


def test_verdicts_and_severities():
    vocab = load_vocabulary()
    assert set(vocab["verdict"]) == set(VERDICTS)
    assert set(vocab["severity"]) == {"blocker", "major", "minor", "nit"}
    assert [vocab["severity"][k]["rank"] for k in ("blocker", "major", "minor", "nit")] == [0, 1, 2, 3]


def test_rows_reference_known_statuses_and_semantics():
    vocab = load_vocabulary()
    statuses = set(vocab["status"])
    semantics = {"ok", "running", "attention", "caution", "danger", "neutral", "accent", "plan", "info"}
    for table in ("job_state", "job_status", "check_status", "env_status"):
        for key, row in vocab[table].items():
            assert row["status"] in statuses, (table, key)
    for table in ("verdict", "severity", "session_state"):
        for key, row in vocab[table].items():
            assert row["semantic"] in semantics, (table, key)
    for spec in vocab["status"].values():
        assert spec["semantic"] in semantics
    assert set(vocab["coverage"].values()) <= statuses


def test_provisioning_is_preparing():
    vocab = load_vocabulary()
    assert vocab["job_state"]["provisioning"]["label"] == "preparing"
    assert vocab["job_status"]["preparing"]["label"] == "preparing"
