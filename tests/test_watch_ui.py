import json

from ui.watch_ui import _extract_last_activity, _detail_lines, load_session_snapshots


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
    assert snap["running"] is True
    assert any(layer["layer"] == "L5" for layer in snap["layers"])


def test_detail_lines_exposes_memory_and_metadata_tabs(tmp_path):
    session_root = tmp_path / "sessions"
    session_dir = session_root / "demo"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session.json"
    session_file.write_text(
        json.dumps(
            {
                "history": [{"role": "user", "parts": [{"type": "text", "text": "hey"}]}],
                "task_memory": {"entries": [{"id": 1, "content": "remember this", "tags": ["ctx"]}]},
                "turn_scratchpad": {"entries": [{"id": 2, "content": "scratch note"}]},
                "variables": {"agent_mode": "debug", "yolo": True},
                "folder_context": {"folders": ["/tmp/work"], "files": ["a.py"]},
            }
        ),
        encoding="utf-8",
    )
    snap = load_session_snapshots(str(session_root))[0]
    memory_lines = "\n".join(_detail_lines(snap, "memory"))
    metadata_lines = "\n".join(_detail_lines(snap, "metadata"))
    assert "remember this" in memory_lines
    assert "scratch note" in memory_lines
    assert "workspace folders: 1" in metadata_lines
