from ui.input import InputHandler


def test_prompt_markup_hides_default_mode():
    handler = InputHandler()
    handler.set_variables({"yolo": False})

    markup = handler.build_prompt_markup("demo", [], agent_mode="default")

    assert "[demo]" in markup
    assert "mode-feature" not in markup
    assert ">>>" in markup


def test_prompt_markup_shows_non_default_mode():
    handler = InputHandler()
    handler.set_variables({"yolo": False})

    markup = handler.build_prompt_markup("demo", [], agent_mode="feature")

    assert "[demo]" in markup
    assert "mode-feature" in markup
    assert ">feature<" in markup


def test_prompt_markup_shows_yolo_indicator_when_enabled():
    handler = InputHandler()
    handler.set_variables({"yolo": True})

    markup = handler.build_prompt_markup("demo", [], agent_mode="default")

    assert "yolo-indicator" in markup
    assert "✦" in markup


def test_input_toolbar_shows_plain_yolo_status_text():
    handler = InputHandler()
    handler.set_variables({"yolo": True})

    toolbar = handler.build_input_toolbar_text()

    assert toolbar == (
        "[Meta+Enter] or [Esc] [Enter] to submit | "
        "[Shift+Tab] toggles YOLO (ON) | "
        "/help for commands"
    )


def test_choice_toolbar_shows_plain_yolo_status_text():
    handler = InputHandler()
    handler.set_variables({"yolo": False})

    toolbar = handler.build_choice_toolbar_text()

    assert toolbar == "[Shift+Tab] toggles YOLO (OFF)"


def test_toggle_yolo_mode_flips_bound_variable():
    handler = InputHandler()
    variables = {"yolo": False}
    handler.set_variables(variables)

    enabled = handler.toggle_yolo_mode()
    assert enabled is True
    assert variables["yolo"] is True

    enabled = handler.toggle_yolo_mode()
    assert enabled is False
    assert variables["yolo"] is False


def test_shift_tab_keybinding_is_registered_for_yolo_toggle():
    handler = InputHandler()

    bindings = [binding.keys for binding in handler.kb.bindings]

    assert any(len(keys) == 1 and keys[0] == "s-tab" for keys in bindings)


def test_command_completion_covers_all_cli_commands_and_aliases():
    handler = InputHandler()

    expected_commands = {
        "/help",
        "/h",
        "/clear",
        "/c",
        "/clearfiles",
        "/cf",
        "/view",
        "/v",
        "/quit",
        "/exit",
        "/q",
        "/file",
        "/f",
        "/add",
        "/folder",
        "/dir",
        "/model",
        "/provider",
        "/agentic",
        "/mode",
        "/feature",
        "/tool",
        "/tools",
        "/system",
        "/sys",
        "/thinking",
        "/list",
        "/ls",
        "/load",
        "/open",
        "/new",
        "/delete",
        "/rm",
        "/stats",
        "/splash",
        "/set",
        "/get",
        "/unset",
        "/variables",
        "/flush",
        "/yolo",
    }

    assert expected_commands.issubset(set(handler.command_completions.keys()))
