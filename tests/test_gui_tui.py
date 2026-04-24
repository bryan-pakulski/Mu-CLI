from core.feature_mode import FeaturePlan, FeatureTask
from ui.gui_tui import _bucket_tasks, _handle_key, GuiState


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


def test_handle_key_switches_tabs_and_navigation_state():
    state = GuiState(tab_index=2, selected_bucket=0, selected_card=0)
    state = _handle_key(state, "\x1b[C")
    assert state.tab_index == 3
    state = _handle_key(state, "\t")
    assert state.selected_bucket == 1
    state = _handle_key(state, "j")
    assert state.selected_card == 1
