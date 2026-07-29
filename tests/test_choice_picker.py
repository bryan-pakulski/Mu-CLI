from __future__ import annotations

import inspect

import pytest

from mu.ui.choice_picker import (
    ChoicePickerState,
    normalize_choices,
    prompt_confirm,
    run_choice_picker,
)


def test_choice_state_uses_default_and_wraps():
    state = ChoicePickerState.create(
        [("sessions", "Sessions"), ("create", "Create new"), ("quit", "Quit")],
        default="create",
    )
    assert state.current().value == "create"
    state.move(1)
    assert state.current().value == "quit"
    state.move(1)
    assert state.current().value == "sessions"
    state.move(-1)
    assert state.current().value == "quit"


def test_choice_state_supports_page_and_bounds():
    state = ChoicePickerState.create([str(index) for index in range(20)])
    state.page(8)
    assert state.current().value == "8"
    state.page(100)
    assert state.current().value == "19"
    state.page(-100)
    assert state.current().value == "0"


def test_choice_normalization_rejects_duplicate_values():
    with pytest.raises(ValueError, match="duplicate"):
        normalize_choices([("same", "One"), ("same", "Two")])


def test_picker_uses_full_screen_and_arrow_navigation():
    source = inspect.getsource(run_choice_picker)
    assert "full_screen=True" in source
    assert '@kb.add("up")' in source
    assert '@kb.add("down")' in source
    assert '@kb.add("pageup")' in source
    assert '@kb.add("pagedown")' in source


def test_prompt_confirm_uses_interactive_choice(monkeypatch):
    monkeypatch.setattr(
        "mu.ui.choice_picker.prompt_choice",
        lambda *args, **kwargs: "yes",
    )
    assert prompt_confirm("Continue?") is True


def test_startup_paths_do_not_use_numbered_picker():
    import mucli
    from mu.container import tui

    provider_source = inspect.getsource(mucli.select_provider_and_model)
    launcher_source = inspect.getsource(mucli.choose_session)
    container_source = inspect.getsource(tui.configure_tui_container)
    manager_source = inspect.getsource(tui.run_container_manager)

    for source in (provider_source, launcher_source, container_source, manager_source):
        assert "IntPrompt.ask" not in source
        assert "prompt_choice" in source
