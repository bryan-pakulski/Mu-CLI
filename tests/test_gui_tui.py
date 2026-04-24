import json
from dataclasses import asdict

from core.feature_mode import FeaturePlan, FeatureTask
from ui.gui_tui import GuiState, _discover_sessions, _handle_key, _tool_usage_counts


def _write_session_fixture(tmp_path):
    session_id = "s1"
    session_dir = tmp_path / session_id
    session_dir.mkdir()

    plan = FeaturePlan(
        feature_id="feat1",
        feature_name="Feature One",
        feature_request="test",
        directory=str(tmp_path),
        metadata_path=str(tmp_path / "feat1.json"),
        tasks=[
            FeatureTask(id=1, title="todo", status="not_started"),
            FeatureTask(id=2, title="doing", status="in_progress"),
        ],
    )
    (tmp_path / "feat1.json").write_text(json.dumps(asdict(plan)), encoding="utf-8")

    session_payload = {
        "feature_registry": {
            "feat1": {
                "feature_id": "feat1",
                "feature_name": "Feature One",
                "status": "in_progress",
                "updated_at": 100,
                "metadata_path": str(tmp_path / "feat1.json"),
            }
        },
        "history": [
            {
                "role": "assistant",
                "parts": [
                    {"type": "tool_call", "tool_name": "run_shell"},
                    {"type": "tool_call", "tool_name": "run_shell"},
                    {"type": "tool_call", "tool_name": "read_file"},
                ],
            }
        ],
    }
    (session_dir / "session.json").write_text(json.dumps(session_payload), encoding="utf-8")
    return str(tmp_path), session_id


def test_discover_sessions_lists_only_directories_with_session_json(tmp_path):
    (tmp_path / "s1").mkdir()
    (tmp_path / "s2").mkdir()
    (tmp_path / "s3").mkdir()
    (tmp_path / "s1" / "session.json").write_text(json.dumps({"history": []}))
    (tmp_path / "s3" / "session.json").write_text(json.dumps({"history": []}))

    assert _discover_sessions(str(tmp_path)) == ["s1", "s3"]


def test_handle_key_hierarchical_navigation(tmp_path):
    session_root, _ = _write_session_fixture(tmp_path)
    state = GuiState()

    state = _handle_key(state, "\n", session_root)
    assert state.screen == "contexts"
    assert state.selected_session == "s1"

    state = _handle_key(state, "\n", session_root)
    assert state.screen == "chat"

    state = _handle_key(state, "\x1b", session_root)
    assert state.screen == "contexts"

    state = _handle_key(state, "\x1b[B", session_root)
    state = _handle_key(state, "\x1b[B", session_root)
    state = _handle_key(state, "\x1b[B", session_root)
    state = _handle_key(state, "\n", session_root)
    assert state.screen == "features"
    state = _handle_key(state, "\n", session_root)
    assert state.screen == "items"
    assert state.selected_feature is not None

    state = _handle_key(state, "\n", session_root)
    assert state.screen == "overview"

    state = _handle_key(state, "\x1b", session_root)
    assert state.screen == "items"

    state = _handle_key(state, "\x1b", session_root)
    assert state.screen == "features"


def test_handle_key_opens_task_detail_and_scrolls(tmp_path):
    session_root, _ = _write_session_fixture(tmp_path)
    state = GuiState()

    state = _handle_key(state, "\n", session_root)
    state = _handle_key(state, "\x1b[B", session_root)
    state = _handle_key(state, "\x1b[B", session_root)
    state = _handle_key(state, "\x1b[B", session_root)
    state = _handle_key(state, "\n", session_root)
    assert state.screen == "features"
    state = _handle_key(state, "\n", session_root)
    state = _handle_key(state, "\x1b[B", session_root)
    state = _handle_key(state, "\x1b[B", session_root)
    state = _handle_key(state, "\x1b[B", session_root)
    state = _handle_key(state, "\n", session_root)

    assert state.screen == "task_detail"

    state = _handle_key(state, "\x1b[B", session_root)
    assert state.detail_offset == 1

    state = _handle_key(state, "\x1b", session_root)
    assert state.screen == "items"


def test_handle_key_quit_confirmation(tmp_path):
    session_root, _ = _write_session_fixture(tmp_path)
    state = GuiState()

    state = _handle_key(state, "q", session_root)
    assert state.confirm_quit is True
    assert state.should_exit is False

    state = _handle_key(state, "\x1b[B", session_root)
    assert state.confirm_index == 1

    state = _handle_key(state, "\n", session_root)
    assert state.should_exit is True


def test_handle_key_supports_application_cursor_sequences(tmp_path):
    session_root, _ = _write_session_fixture(tmp_path)
    state = GuiState()

    state = _handle_key(state, "\n", session_root)
    assert state.screen == "contexts"

    state = _handle_key(state, "\x1bOB", session_root)
    assert state.context_index == 1

    state = _handle_key(state, "\x1bOB", session_root)
    state = _handle_key(state, "\x1bOB", session_root)
    assert state.context_index == 3

    state = _handle_key(state, "\n", session_root)
    assert state.screen == "features"

    state = _handle_key(state, "\x1bOB", session_root)
    assert state.feature_index == 0

    state = _handle_key(state, "\n", session_root)
    assert state.screen == "items"


def test_tool_usage_counts_sorts_descending():
    payload = {
        "history": [
            {
                "parts": [
                    {"type": "tool_call", "tool_name": "a"},
                    {"type": "tool_call", "tool_name": "b"},
                    {"type": "tool_call", "tool_name": "a"},
                ]
            }
        ]
    }
    assert _tool_usage_counts(payload) == [("a", 2), ("b", 1)]
