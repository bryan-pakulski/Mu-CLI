"""Durable, time-anchored subagent cards for web and mobile history."""
from __future__ import annotations

import json
from pathlib import Path

from mu.agent.subagent_artifacts import SubagentArtifactStore
from mu.gui.routers.sessions import _subagent_history_anchors
from mu.session.manager import SessionManager


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _finish(
    store: SubagentArtifactStore,
    task_id: str,
    batch_id: str,
    *,
    title: str,
    parent_turn_index: int,
    started_at: float,
) -> None:
    store.start(task_id, {
        "task": f"Long delegated instructions for {title}",
        "title": title,
        "depth": 1,
        "batch_id": batch_id,
        "model": "test-model",
        "specialist_key": "review",
        "parent_history_index": parent_turn_index + 1,
        "parent_turn_index": parent_turn_index,
        "parent_turn_id": f"turn-{parent_turn_index}",
        "started_at": started_at,
    })
    store.record_event(
        task_id,
        {"kind": "subagent_progress"},
        state_patch={
            "iter": 4,
            "max_iter": 30,
            "tool_count": 1,
            "actions": [{
                "seq": 1,
                "tool": "read_file",
                "detail": "mu/gui/static/js/app.js",
                "status": "done",
                "elapsed": 0.4,
            }],
        },
    )
    store.finish(
        task_id,
        {"status": "done", "summary": f"Completed {title}", "tool_calls": 1},
        [],
    )


def test_subagent_batches_replay_at_their_exact_spawn_result(tmp_path):
    session_dir = tmp_path / "sessions" / "demo"
    store = SubagentArtifactStore(str(session_dir))
    _finish(store, "sa-first", "batch-first", title="Security audit", parent_turn_index=0, started_at=1)
    _finish(store, "sa-second", "batch-second", title="API review", parent_turn_index=2, started_at=2)
    history = [
        {"role": "user", "timeline_id": "turn-0", "parts": [{"type": "text", "text": "First request"}]},
        {"role": "assistant", "parts": [{
            "type": "tool_result",
            "tool_name": "spawn_agent",
            "tool_result": json.dumps({"data": {"task_id": "sa-first"}}),
        }]},
        {"role": "user", "timeline_id": "turn-2", "parts": [{"type": "text", "text": "Second request"}]},
        {"role": "assistant", "parts": [{
            "type": "tool_result",
            "tool_name": "spawn_agent",
            "tool_result": json.dumps({"data": {"task_id": "sa-second"}}),
        }]},
    ]

    exact, fallback = _subagent_history_anchors(str(session_dir), history)

    assert not fallback
    assert exact[(1, 0)][0]["batch_id"] == "batch-first"
    assert exact[(3, 0)][0]["batch_id"] == "batch-second"
    first = exact[(1, 0)][0]["agents"][0]
    assert first["title"] == "Security audit"
    assert first["actions"][0]["tool"] == "read_file"
    assert first["max_iter"] == 30


def test_compacted_subagent_batch_uses_its_original_user_turn_fallback(tmp_path):
    session_dir = tmp_path / "sessions" / "demo"
    store = SubagentArtifactStore(str(session_dir))
    _finish(store, "sa-compact", "batch-compact", title="Test review", parent_turn_index=0, started_at=1)
    compacted = [
        {"role": "user", "timeline_id": "turn-0", "parts": [{"type": "text", "text": "Review tests"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "Done"}]},
    ]

    exact, fallback = _subagent_history_anchors(str(session_dir), compacted)

    assert not exact
    assert fallback[0][0]["batch_id"] == "batch-compact"


def test_cleared_history_never_reuses_an_old_numeric_anchor(tmp_path):
    session_dir = tmp_path / "sessions" / "demo"
    store = SubagentArtifactStore(str(session_dir))
    _finish(store, "sa-old", "batch-old", title="Old review", parent_turn_index=0, started_at=1)
    replacement = [
        {"role": "user", "timeline_id": "turn-new", "parts": [{"type": "text", "text": "New conversation"}]},
    ]

    exact, fallback = _subagent_history_anchors(str(session_dir), replacement)

    assert exact == {}
    assert fallback == {}


def test_turn_timeline_id_survives_completed_turn_compaction():
    manager = SessionManager()
    manager.history = [
        {"role": "user", "timeline_id": "turn-stable", "parts": [{"type": "text", "text": "Review"}]},
        {"role": "assistant", "parts": [{"type": "tool_call", "tool_name": "spawn_agent", "tool_args": {}}]},
        {"role": "tool", "parts": [{"type": "tool_result", "tool_name": "spawn_agent", "tool_result": "ok"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "Complete"}]},
    ]

    manager.compact_completed_turn()

    assert manager.history[0]["timeline_id"] == "turn-stable"


def test_history_lookup_without_subagents_is_non_mutating(tmp_path):
    session_dir = tmp_path / "sessions" / "demo"
    session_dir.mkdir(parents=True)

    assert _subagent_history_anchors(str(session_dir), []) == ({}, {})
    assert not (session_dir / "subagents").exists()


def test_clients_keep_subagent_cards_as_durable_timeline_anchors():
    web = read("mu/gui/static/js/app.js")
    mobile = read("mobile/android/src/hooks/useChatSession.ts")
    product = read("mobile/android/src/screens/ChatScreenProduct.tsx")

    assert '_historySubagentPanel(part)' in web
    assert 'part.type === "subagent_panel"' in web
    assert "slot.turns.splice(index, 1)" not in web
    assert "turn.role !== \"subagent_panel\" || this._panelRunning(turn)" not in web
    assert "message.role === 'visualization' || message.role === 'subagent_panel'" in mobile
    assert "upsertSubagentPanelMessage(current, event)" in mobile
    assert "part.type === 'subagent_panel'" in mobile
    assert "item.role === 'subagent_panel'" in product
    assert "styles.liveSubagentDock" not in product
    assert '"timeline_id": "turn-" + uuid.uuid4().hex' in read("mu/agent/loop_body.py")
