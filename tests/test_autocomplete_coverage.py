"""Pin slash-command autocomplete behavior end-to-end.

The bug that drove this: prompt_toolkit's `NestedCompleter` uses
`WordCompleter` under the hood with the default `\\w+` word pattern,
which treats `/` as a word boundary. So typing `/me` and hitting Tab
never offered `/memory` — the leading slash broke prefix matching.

`SlashCommandCompleter` (in `ui/input.py`) bypasses that and:
  * suggests commands by partial slash-prefix match
  * descends into the per-command sub-completer once a space is typed
"""

from prompt_toolkit.document import Document

import pytest

from ui.input import InputHandler
from utils.config import DEFAULT_VARIABLES


def _completions(handler, text):
    doc = Document(text=text, cursor_position=len(text))
    return [c.text for c in handler.completer.get_completions(doc, None)]


@pytest.fixture
def handler():
    h = InputHandler()
    h.set_variables(dict(DEFAULT_VARIABLES))
    return h


# ============================================================ slash-command discovery


def test_empty_input_offers_every_command(handler):
    """Pressing Tab on an empty prompt should reveal every command."""
    suggestions = _completions(handler, "")
    # All 32 commands the curated set advertises must appear.
    assert len(suggestions) >= 30, f"only {len(suggestions)} commands; check curated set"
    assert "/help" in suggestions
    assert "/memory" in suggestions
    assert "/plan" in suggestions
    assert "/ollama" in suggestions


def test_partial_slash_prefix_matches(handler):
    """`/m` should offer /memory /mode /model (the canonical user case)."""
    suggestions = _completions(handler, "/m")
    assert "/memory" in suggestions
    assert "/mode" in suggestions
    assert "/model" in suggestions


def test_longer_prefix_narrows(handler):
    suggestions = _completions(handler, "/me")
    assert suggestions == ["/memory"]


def test_full_command_no_space_still_completes_itself(handler):
    """Typing /memory then Tab still offers /memory (no descent yet)."""
    suggestions = _completions(handler, "/memory")
    assert "/memory" in suggestions


def test_unknown_prefix_returns_nothing(handler):
    assert _completions(handler, "/zzznotacmd") == []


# ============================================================ sub-command descent


def test_memory_subcommands(handler):
    """The bug-fix case the user explicitly reported."""
    suggestions = _completions(handler, "/memory ")
    for needed in ("status", "list", "clear"):
        assert needed in suggestions, f"/memory should offer {needed!r}"


def test_memory_clear_targets(handler):
    suggestions = _completions(handler, "/memory clear ")
    for needed in ("task", "scratchpad", "all"):
        assert needed in suggestions


def test_plan_subcommands(handler):
    suggestions = _completions(handler, "/plan ")
    assert set(suggestions) >= {"on", "off", "toggle"}


def test_ollama_subcommands(handler):
    suggestions = _completions(handler, "/ollama ")
    assert set(suggestions) >= {"status", "models", "pull", "options"}


def test_workspace_clear(handler):
    suggestions = _completions(handler, "/workspace ")
    assert "clear" in suggestions


def test_mode_subcommands(handler):
    suggestions = _completions(handler, "/mode ")
    assert "default" in suggestions
    assert "debug" in suggestions


def test_research_subcommands(handler):
    suggestions = _completions(handler, "/research ")
    assert "status" in suggestions
    assert "sources" in suggestions


def test_tool_subcommands(handler):
    suggestions = _completions(handler, "/tool ")
    assert "enable" in suggestions
    assert "disable" in suggestions
    assert "list" in suggestions


def test_feature_subcommands(handler):
    suggestions = _completions(handler, "/feature ")
    # `feature_completer` is a NestedCompleter — its keys should include
    # at least the canonical subcommands.
    for needed in ("list", "show", "new", "load", "delete"):
        assert needed in suggestions, f"/feature missing {needed!r}"


def test_provider_choices(handler):
    suggestions = _completions(handler, "/provider ")
    assert set(suggestions) >= {"gemini", "ollama", "openai"}


# ============================================================ /set position-aware


def test_set_offers_variable_names_pre_space(handler):
    suggestions = _completions(handler, "/set ")
    assert "yolo" in suggestions
    assert "streaming_enabled" in suggestions
    assert "agent_mode" in suggestions


def test_set_partial_variable_name_narrows(handler):
    suggestions = _completions(handler, "/get strea")
    assert "streaming_enabled" in suggestions


def test_set_bool_value_completion(handler):
    """`/set yolo ` → true | false (the value column, not variable names)."""
    suggestions = _completions(handler, "/set yolo ")
    assert "true" in suggestions
    assert "false" in suggestions
    # And NO variable names should leak into the value column.
    assert "agent_mode" not in suggestions


def test_set_bool_value_prefix_narrows(handler):
    suggestions = _completions(handler, "/set yolo f")
    assert suggestions == ["false"]


def test_set_agent_mode_value_completion(handler):
    """`/set agent_mode ` should offer the registered modes."""
    suggestions = _completions(handler, "/set agent_mode ")
    assert "default" in suggestions
    assert "debug" in suggestions
    assert "research" in suggestions


def test_set_agent_mode_value_prefix(handler):
    suggestions = _completions(handler, "/set agent_mode r")
    assert "research" in suggestions
    assert "default" not in suggestions


def test_set_numeric_value_has_no_suggestion(handler):
    """Numeric variables get no value suggestion (free-form input)."""
    suggestions = _completions(handler, "/set ollama_num_ctx ")
    assert suggestions == []


# ============================================================ /unset


def test_unset_offers_variable_names(handler):
    suggestions = _completions(handler, "/unset ")
    assert "yolo" in suggestions
    assert "streaming_enabled" in suggestions


def test_unset_all_flag(handler):
    suggestions = _completions(handler, "/unset --")
    assert "--all" in suggestions


# ============================================================ every command discoverable


def test_every_listed_command_appears_in_empty_completion(handler):
    """Every key in `command_completions` must be reachable from an empty
    Tab prompt. Otherwise commands are silently undiscoverable."""
    expected = set(handler.command_completions.keys())
    discovered = set(_completions(handler, ""))
    missing = expected - discovered
    assert not missing, f"these commands aren't reachable via empty Tab: {sorted(missing)}"


def test_every_listed_command_reachable_via_partial(handler):
    """Every command must also be reachable by typing its prefix and
    hitting Tab (no point in a command no user can discover)."""
    for cmd in handler.command_completions:
        # Try the partial up to and including the first 2 chars after `/`.
        if len(cmd) <= 2:
            continue
        prefix = cmd[:3]  # e.g. /me, /st, /pl
        suggestions = _completions(handler, prefix)
        assert cmd in suggestions, f"{cmd!r} not reachable from prefix {prefix!r}"
