"""Orchestration on every surface: the shared attention counts, the badge endpoint (never starts
orchestration), the TUI's ``/orch`` command, startup banner and toolbar segment."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from mu.orchestration import attention
from mu.orchestration.service import Orchestrator

from orch_helpers import add_node, publish_tool, tool_manifest


class _UI:
    def __init__(self):
        self.info, self.errors = [], []

    def show_info(self, text):
        self.info.append(str(text))

    def show_error(self, text):
        self.errors.append(str(text))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    return tmp_path / "home" / "orchestration"


def _orch_with_work():
    orch = Orchestrator()
    add_node(orch, "new1", {"env": "dev"}, status="pending")
    web, _ = add_node(orch, "web1", {"env": "dev"})
    publish_tool(orch, tool_manifest("app.fix", effect="mutating"))
    run = orch.create_run(actor="ai:s", kind="tool", target="app.fix@1", selector={"labels": "env=dev"})
    return orch, web, run


def _orch_cmd(text):
    from mu.commands import dispatch
    ui = _UI()
    result = dispatch(SimpleNamespace(ui=ui), text)
    return result, ui


def test_peek_never_creates_orchestration(home):
    assert attention.peek() is None and not home.exists()
    from mu.commands.orch import attention_banner, toolbar_segment
    assert attention_banner() == "" and toolbar_segment() == "" and not home.exists()
    result, ui = _orch_cmd("/orch")
    assert result.ok and result.data == {"used": False} and "isn't set up" in ui.info[0] and not home.exists()


def test_counts_and_descriptions_are_shared(home):
    orch, _web, run = _orch_with_work()
    att = attention.peek()
    assert att == orch.attention() and att["total"] == 2 and att["approvals"] == 1 and att["pending_nodes"] == 1
    assert attention.describe(att) == "1 run awaiting approval · 1 node awaiting acceptance"
    from mu.commands.orch import attention_banner, toolbar_segment
    assert "Orchestration needs you: 1 run awaiting approval" in attention_banner()
    assert toolbar_segment().startswith("◆ 2 orchestration need you [/orch]")


def test_tui_status_and_decisions(home):
    orch, web, run = _orch_with_work()
    pending = next(n for n in orch.store.list_nodes() if n.status == "pending")
    result, ui = _orch_cmd("/orch")
    text = ui.info[-1]
    assert result.ok and "Runs awaiting approval (1)" in text and run.id in text
    assert "Nodes awaiting acceptance (1)" in text and pending.id in text
    assert "No master is running here" in text
    result, ui = _orch_cmd(f"/orch accept {pending.id}")
    assert result.ok and orch.store.get_node(pending.id).status == "active"
    result, ui = _orch_cmd(f"/orch approve {run.id}")
    assert result.ok and orch.store.get_run(run.id).state == "approved"
    assert "No master is running here" in ui.info[-1]
    result, ui = _orch_cmd(f"/orch run {run.id}")
    assert result.ok and "web1" in ui.info[-1]
    result, ui = _orch_cmd("/orch pause maintenance")
    assert result.ok and orch.dispatch_paused()
    from mu.commands.orch import toolbar_segment
    attention._cache.clear()                            # the toolbar reuses a peek for 20s
    assert "dispatch paused" in toolbar_segment()
    result, ui = _orch_cmd("/orch resume")
    assert result.ok and not orch.dispatch_paused()
    result, ui = _orch_cmd("/orch approve r_missing")
    assert not result.ok and "unknown run" in ui.errors[-1]
    result, ui = _orch_cmd("/orch nonsense")
    assert not result.ok and "unknown /orch command" in ui.errors[-1]
    audit = [e["actor"] for e in orch.store.audit_entries(limit=10_000)
             if e["action"] in ("run.approve", "node.accept") and e["subject"] in (pending.id, run.id)]
    assert audit == ["operator", "operator"]           # the TUI acts as the operator, through dispatch


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from mu.gui.app import create_app
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    app = create_app(args=SimpleNamespace(provider=None, model=None), build_session_fn=lambda *a, **k: None)
    return TestClient(app), app, tmp_path / "home" / "orchestration"


def test_badge_endpoint_never_starts_orchestration(client):
    c, app, home = client
    body = c.get("/api/orchestration/badge").json()
    assert body["used"] is False and body["total"] == 0 and not home.exists()
    assert getattr(app.state, "orchestrator", None) is None
    _orch_with_work()
    body = c.get("/api/orchestration/badge").json()
    assert body["used"] and body["total"] == 2 and body["summary"].startswith("1 run awaiting approval")
    assert getattr(app.state, "orchestrator", None) is None         # still only peeked
    page = c.get("/work").text                                        # every page carries the badge slot
    assert 'data-orch-badge' in page and 'href="/orchestration"' in page


def _menu_answers(monkeypatch, choices, texts=()):
    choices, texts = iter(choices), iter(texts)
    seen = []

    def pick(title, options, **kwargs):
        seen.append((title, [key for key, *_ in options]))
        return next(choices)

    monkeypatch.setattr("mu.ui.choice_picker.prompt_choice", pick)
    monkeypatch.setattr("mu.ui.choice_picker.prompt_confirm", lambda *a, **k: True)
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *a, **k: next(texts))
    return seen


def test_launcher_menu_unused_never_creates_orchestration(home, monkeypatch):
    from mu.commands.orch import launcher_hint, run_orchestration_menu
    seen = _menu_answers(monkeypatch, [])
    assert "not set up" in launcher_hint()
    run_orchestration_menu()
    assert seen == [] and not home.exists()


def test_launcher_menu_decides_pending_work_as_the_operator(home, monkeypatch):
    orch, _web, run = _orch_with_work()
    pending = next(n for n in orch.store.list_nodes() if n.status == "pending")
    from mu.commands.orch import launcher_hint, run_orchestration_menu
    attention._cache.clear()
    assert launcher_hint() == "Needs you: 1 run awaiting approval · 1 node awaiting acceptance"
    seen = _menu_answers(monkeypatch, [f"run:{run.id}", "approveRun", f"node:{pending.id}", "acceptNode",
                                       "pause", "__back__"], texts=["maintenance"])
    run_orchestration_menu()
    assert orch.store.get_run(run.id).state == "approved"
    assert orch.store.get_node(pending.id).status == "active"
    assert orch.dispatch_paused()
    inbox = seen[0][1]
    assert inbox[:2] == [f"run:{run.id}", f"node:{pending.id}"] and inbox[-1] == "__back__"
    assert "resume" in seen[-1][1] and "pause" not in seen[-1][1]       # the kill switch flips
    audit = [e["actor"] for e in orch.store.audit_entries(limit=10_000)
             if e["action"] in ("run.approve", "node.accept") and e["subject"] in (pending.id, run.id)]
    assert audit == ["operator", "operator"]
