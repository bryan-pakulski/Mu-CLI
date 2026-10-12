"""Job settings stage models must beat the submitting session's model.

Regression: GUI/TUI job creation stamped the loaded session's provider/model
(e.g. ollama glm-5.3) as execution.provider/model. resolve_stage_model ranks
the job-level default above Job settings, so Anthropic stage models in
settings were silently ignored.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from mu.commands import job as job_cmd
from mu.gui.routers import jobs as jobs_router
from mu.jobs import job_settings
from mu.jobs.models import resolve_stage_model

SETTINGS = {
    "plan": {"provider": "anthropic", "model": "claude-opus-5-5"},
    "implement": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
    "review": {"provider": "anthropic", "model": "claude-fable-5-1"},
}


def _home(monkeypatch, tmp_path, stage_models):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path))
    path = tmp_path / "jobs" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"stage_models": stage_models}), encoding="utf-8")
    assert job_settings.settings_path() == str(path)


def _session(tmp_path):
    return SimpleNamespace(
        provider=SimpleNamespace(name="ollama", model_name="glm-5.3"),
        variables={"agent_mode": "default", "session_type": "workspace"},
        folder_context=SimpleNamespace(folders=[str(tmp_path)]),
    )


def _request(session):
    state = SimpleNamespace(session_by_name=lambda name: session)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _resolved(execution):
    return {s: resolve_stage_model(execution, s)["model"] for s in ("plan", "implement", "review")}


def test_router_defaults_do_not_override_settings(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path, SETTINGS)
    value = jobs_router._with_session_defaults(
        _request(_session(tmp_path)), {"title": "t", "session_name": "mucli"}
    )
    execution = value["execution"]
    assert "model" not in execution and "provider" not in execution
    assert _resolved(execution) == {
        "plan": "claude-opus-5-5",
        "implement": "claude-sonnet-5-5",
        "review": "claude-fable-5-1",
    }


def test_router_session_model_fills_only_unset_stages(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path, {"plan": SETTINGS["plan"]})
    value = jobs_router._with_session_defaults(
        _request(_session(tmp_path)), {"title": "t", "session_name": "mucli"}
    )
    assert _resolved(value["execution"]) == {
        "plan": "claude-opus-5-5",
        "implement": "glm-5.3",
        "review": "glm-5.3",
    }


def test_router_keeps_explicit_job_model(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path, SETTINGS)
    value = jobs_router._with_session_defaults(
        _request(_session(tmp_path)),
        {"title": "t", "session_name": "mucli", "execution": {"provider": "openai", "model": "gpt-x"}},
    )
    assert value["execution"]["model"] == "gpt-x"
    assert set(_resolved(value["execution"]).values()) == {"gpt-x"}


def test_tui_defaults_do_not_override_settings(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path, SETTINGS)
    execution = job_cmd._session_defaults(_session(tmp_path))["execution"]
    assert "model" not in execution
    assert _resolved(execution)["implement"] == "claude-sonnet-5-5"
