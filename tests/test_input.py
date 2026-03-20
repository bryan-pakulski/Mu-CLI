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
