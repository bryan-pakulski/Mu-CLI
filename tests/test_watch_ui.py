import json

from ui.watch_ui import _extract_last_activity, load_session_snapshots


def test_extract_last_activity_prefers_latest_tool_call():
    history = [
        {"role": "user", "parts": [{"type": "text", "text": "hello"}]},
        {
            "role": "assistant",
            "parts": [{"type": "tool_call", "tool_name": "read_file"}],
        },
    ]
    assert _extract_last_activity(history) == "assistant: tool_call(read_file)"


def test_load_session_snapshots_reads_feature_status(tmp_path):
    session_root = tmp_path / "sessions"
    session_dir = session_root / "demo"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session.json"

    payload = {
        "history": [{"role": "assistant", "parts": [{"type": "text", "text": "done"}]}],
        "variables": {"agent_mode": "feature"},
        "provider_config": {"provider": "openai", "model": "gpt-5"},
        "token_counts": {"total": 321},
        "feature_state": {
            "status": "in_progress",
            "feature_id": "feat_x",
            "feature_plan": {"feature_name": "Realtime Watcher"},
        },
    }
    session_file.write_text(json.dumps(payload), encoding="utf-8")

    snapshots = load_session_snapshots(str(session_root))
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap["name"] == "demo"
    assert snap["agent_mode"] == "feature"
    assert snap["feature"] == "Realtime Watcher"
    assert snap["feature_status"] == "in_progress"
    assert snap["tokens"] == 321
