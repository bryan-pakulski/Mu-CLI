import json

from rich.panel import Panel

from ui.watch_ui import _extract_last_activity, _detail_lines, _handle_key, WatchState, _render_detail, load_session_snapshots


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
    variables_lines = "\n".join(_detail_lines(snap, "variables"))
    assert "remember this" in memory_lines
    assert "scratch note" in memory_lines
    assert "workspace folders: 1" in metadata_lines
    assert "agent_mode" in variables_lines


def test_enter_opens_session_view_and_search_mode():
    state = WatchState()
    state = _handle_key(state, "\n", 2)
    assert state.in_session_view is True
    state = _handle_key(state, "/", 2)
    assert state.search_mode is True
    state = _handle_key(state, "m", 2)
    state = _handle_key(state, "\n", 2)
    assert state.search_mode is False
    assert state.search_query == "m"
    state = _handle_key(state, "e", 2)
    assert state.expand_focused is True


def test_render_detail_board_tab(tmp_path):
    session_root = tmp_path / "sessions"
    session_dir = session_root / "demo"
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        json.dumps(
            {
                "feature_state": {
                    "feature_plan": {
                        "phases": [
                            {"id": 1, "number": 1, "title": "Plan", "status": "not_started", "exit_criteria": ["A"]},
                            {"id": 2, "number": 2, "title": "Build", "status": "in_progress", "exit_criteria": ["A", "B"], "verified_exit_criteria": ["A"]},
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    snap = load_session_snapshots(str(session_root))[0]
    state = WatchState(tab_index=0, in_session_view=True)
    panel = _render_detail(snap, state)
    assert isinstance(panel, Panel)


def test_loop_active_session_marked_running_even_if_file_is_stale(tmp_path):
    session_root = tmp_path / "sessions"
    session_dir = session_root / "demo"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session.json"
    session_file.write_text(
        json.dumps(
            {
                "variables": {"loop_active": True},
                "history": [],
            }
        ),
        encoding="utf-8",
    )
    # Stale timestamp should still be considered active because loop_active=true.
    old_ts = 1_600_000_000
    import os
    os.utime(session_file, (old_ts, old_ts))

    snap = load_session_snapshots(str(session_root))[0]
    assert snap["running"] is True


def test_watch_state_defaults_to_name_sorting():
    state = WatchState()
    assert state.sort_key == "name"
