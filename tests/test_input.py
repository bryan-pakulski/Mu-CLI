from ui.input import InputHandler


def test_prompt_markup_hides_default_mode():
    handler = InputHandler()

    markup = handler.build_prompt_markup("demo", [], agent_mode="default")

    assert "[demo]" in markup
    assert "mode-feature" not in markup
    assert ">>>" in markup


def test_prompt_markup_shows_non_default_mode():
    handler = InputHandler()

    markup = handler.build_prompt_markup("demo", [], agent_mode="feature")

    assert "[demo]" in markup
    assert "mode-feature" in markup
    assert ">feature<" in markup


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
