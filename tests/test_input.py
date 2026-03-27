from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from types import SimpleNamespace

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


def test_prompt_markup_includes_current_task_when_present():
    handler = InputHandler()
    handler.set_variables({"yolo": False})

    markup = handler.build_prompt_markup(
        "demo",
        [],
        agent_mode="feature",
        current_task="Implement fixtures/pcap.py",
    )

    assert "[Task: Implement fixtures/pcap.py]" in markup


def test_prompt_markup_compacts_feature_progress_bars():
    handler = InputHandler()
    handler.set_variables({"yolo": False})

    markup = handler.build_prompt_markup(
        "demo",
        [],
        agent_mode="feature",
        feature_context={
            "status": "awaiting_input",
            "task": "Implement fixtures/pcap.py",
            "phase_done": 2,
            "phase_total": 4,
            "overall_done": 3,
            "overall_total": 10,
        },
    )

    assert "Feature:" not in markup
    assert "Task: Implement fixtures/pcap.py" not in markup
    assert "P ████░░░░  50%" in markup
    assert "O ██░░░░░░  30%" in markup


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
        "/clear-workspace",
        "/cw",
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
        "/workspace",
        "/update",
        "/agentic",
        "/mode",
        "/feature",
        "/features",
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


def test_unset_completion_includes_all_keyword():
    handler = InputHandler()
    document = Document(
        text="/unset --",
        cursor_position=len("/unset --"),
    )
    completions = list(
        handler.completer.get_completions(
            document,
            CompleteEvent(completion_requested=True),
        )
    )
    completion_texts = {completion.text for completion in completions}

    assert "--all" in completion_texts


def test_folder_completion_includes_clear_subcommand():
    handler = InputHandler()
    document = Document(
        text="/folder c",
        cursor_position=len("/folder c"),
    )
    completions = list(
        handler.completer.get_completions(
            document,
            CompleteEvent(completion_requested=True),
        )
    )
    completion_texts = {completion.text for completion in completions}

    assert "clear" in completion_texts


def test_tool_enable_completion_suggests_tool_names(monkeypatch):
    monkeypatch.setattr(
        "core.tools.TOOLS",
        [SimpleNamespace(name="read_file"), SimpleNamespace(name="write_file")],
    )
    handler = InputHandler()
    document = Document(
        text="/tool enable wr",
        cursor_position=len("/tool enable wr"),
    )
    completions = list(
        handler.completer.get_completions(
            document,
            CompleteEvent(completion_requested=True),
        )
    )
    completion_texts = {completion.text for completion in completions}

    assert "write_file" in completion_texts


def test_memory_clear_completion_includes_scratch_alias():
    handler = InputHandler()
    document = Document(
        text="/memory clear scr",
        cursor_position=len("/memory clear scr"),
    )
    completions = list(
        handler.completer.get_completions(
            document,
            CompleteEvent(completion_requested=True),
        )
    )
    completion_texts = {completion.text for completion in completions}

    assert "scratch" in completion_texts


def test_workspace_completion_includes_clear_subcommand():
    handler = InputHandler()
    document = Document(
        text="/workspace c",
        cursor_position=len("/workspace c"),
    )
    completions = list(
        handler.completer.get_completions(
            document,
            CompleteEvent(completion_requested=True),
        )
    )
    completion_texts = {completion.text for completion in completions}

    assert "clear" in completion_texts


def test_feature_completion_includes_exit_subcommand():
    handler = InputHandler()
    document = Document(
        text="/feature e",
        cursor_position=len("/feature e"),
    )
    completions = list(
        handler.completer.get_completions(
            document,
            CompleteEvent(completion_requested=True),
        )
    )
    completion_texts = {completion.text for completion in completions}

    assert "exit" in completion_texts


def test_feature_delete_completion_suggests_feature_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "documentation" / "feature_req_alpha").mkdir(parents=True)
    (tmp_path / "documentation" / "feature_req_beta").mkdir(parents=True)

    handler = InputHandler()
    document = Document(text="/feature delete a", cursor_position=len("/feature delete a"))
    completions = list(
        handler.completer.get_completions(
            document,
            CompleteEvent(completion_requested=True),
        )
    )
    completion_texts = {completion.text for completion in completions}

    assert "alpha" in completion_texts
    assert "beta" in completion_texts
