import json

from core.feature_mode import FeaturePlan, FeatureTask
from ui.gui_tui import _bucket_tasks, _discover_sessions, _handle_key, GuiState


def _sample_plan() -> FeaturePlan:
    return FeaturePlan(
        feature_id="f1",
        feature_name="Feature One",
        feature_request="test",
        directory=".",
        tasks=[
            FeatureTask(id=1, title="todo", status="not_started"),
            FeatureTask(id=2, title="blocked", status="blocked"),
            FeatureTask(id=3, title="doing", status="in_progress"),
            FeatureTask(id=4, title="done", status="completed"),
        ],
    )


def test_bucket_tasks_maps_statuses_to_board_columns():
    buckets = _bucket_tasks(_sample_plan())
    assert [t.id for t in buckets["Backlog"]] == [1]
    assert [t.id for t in buckets["Selected for Development"]] == [2]
    assert [t.id for t in buckets["In Progress"]] == [3]
    assert [t.id for t in buckets["Done"]] == [4]


def test_discover_sessions_lists_only_directories_with_session_json(tmp_path):
    (tmp_path / "s1").mkdir()
    (tmp_path / "s2").mkdir()
    (tmp_path / "s3").mkdir()
    (tmp_path / "s1" / "session.json").write_text(json.dumps({"history": []}))
    (tmp_path / "s3" / "session.json").write_text(json.dumps({"history": []}))

    assert _discover_sessions(str(tmp_path)) == ["s1", "s3"]


def test_handle_key_navigates_sessions_and_pin_focus():
    state = GuiState(session_names=["a", "b", "c"], focus="sessions", session_index=0)
    state = _handle_key(state, "j")
    assert state.session_index == 1
    state = _handle_key(state, "\n")
    assert state.pinned_session == "b"
    assert state.focus == "board"
    state = _handle_key(state, "b")
    assert state.focus == "sessions"


def test_handle_key_opens_and_closes_card_detail_mode():
    state = GuiState(session_names=["a"], focus="sessions", session_index=0)
    state = _handle_key(state, "\n")  # pin/open session
    assert state.focus == "board"
    state = _handle_key(state, "\n")  # open card detail
    assert state.detail_open is True
    state = _handle_key(state, "j")
    assert state.detail_offset == 1
    state = _handle_key(state, "b")
    assert state.detail_open is False
