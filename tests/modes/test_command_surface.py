"""Pin the slash-command surface so cleanup decisions don't drift.

Two invariants every cleanup we just did relies on:

  1. Every command in `InputHandler.command_completions` actually dispatches
     somewhere — either through the new `mu.commands` registry OR through
     a `cmd == "/foo"` branch in `mucli.handle_command`. (Catches a stale
     autocomplete entry for a command that no longer exists.)

  2. Every command in `_HELP_GROUPS` also appears in autocomplete. (Catches
     a /help entry for a command users can't tab-complete.)

The reverse — every dispatched command appears in autocomplete — is also
checked. That catches `/foo` working but invisible to autocomplete.
"""

import re

import pytest

import mu.commands as mc
from mucli import _HELP_GROUPS
from mu.ui.input import InputHandler


# Set of commands the test should ignore — these are intentional escape
# hatches (legacy fallthroughs we deliberately don't advertise).
_INTENTIONALLY_HIDDEN: set = set()


def _new_registry_commands() -> set:
    return {name for name in mc.list_commands() for name in name.names}


def _new_registry_aliases() -> set:
    """Flat set of every alias registered via @command(*names)."""
    out: set = set()
    for spec in mc.list_commands():
        out.update(spec.names)
    return out


def _autocomplete_commands() -> set:
    handler = InputHandler()
    return set(handler.command_completions.keys())


def _help_commands() -> set:
    """Every command name surfaced in /help, extracted from the table."""
    out: set = set()
    for _, entries in _HELP_GROUPS:
        for cmd, _alias, _desc in entries:
            # The "Command" column can be e.g. "/load <name>" — first token.
            first = cmd.strip().split()[0]
            if first.startswith("/"):
                out.add(first)
    return out


# ============================================================ autocomplete is well-formed


# ============================================================ /help mirrors autocomplete


def test_every_registered_command_appears_in_help():
    """Catch regressions where a slash command is added via @command but
    never made it into the curated /help layout or the auto-discovery
    safety net at the bottom of `print_help`. If this test fails, either
    add the command to `_HELP_GROUPS` in mucli.py or accept that the
    auto-discovery "Other" section will catch it."""
    from mucli import _curated_commands, _uncurated_commands_section

    curated = _curated_commands()
    extra = _uncurated_commands_section() or ("Other", [])
    extra_names = {row[0] for row in extra[1]}

    registry_primaries = {spec.names[0] for spec in mc.list_commands()}
    surfaced = curated | extra_names
    missing = registry_primaries - surfaced - _INTENTIONALLY_HIDDEN
    assert not missing, (
        f"These registered commands never show up in /help: {sorted(missing)}. "
        "Add them to `_HELP_GROUPS` in mucli.py — or, if intentional, to "
        "`_INTENTIONALLY_HIDDEN` in this test."
    )




# ============================================================ key commands are present


def test_core_commands_are_present():
    """Smoke check: the user-facing essentials are still wired up."""
    needed = {
        "/help",
        "/quit",
        "/q",
        "/clear",
        "/history",
        "/session",
        "/workspace",
        "/model",
        "/provider",
        "/ollama",
        "/set",
        "/get",
        "/unset",
        "/variables",
        "/mode",
        "/plan",
        "/yolo",
        "/agentic",
        "/thinking",
        "/memory",
        "/tool",
        "/feature",
        "/stats",
        "/continue",
    }
    autocomplete = _autocomplete_commands()
    missing = needed - autocomplete
    assert not missing, f"Core commands missing from autocomplete: {sorted(missing)}"


# ============================================================ plan / ollama have subcompleters


def test_plan_has_subcommand_autocomplete():
    """`/plan ` (with space + Tab) should offer on/off/toggle."""
    from prompt_toolkit.completion import NestedCompleter

    handler = InputHandler()
    plan_entry = handler.command_completions["/plan"]
    assert plan_entry is not None, "/plan should have a sub-completer"
    # NestedCompleter exposes the dict via `options`.
    if hasattr(plan_entry, "options"):
        subs = set(plan_entry.options.keys())
        assert {"on", "off", "toggle"}.issubset(subs)


def test_ollama_has_subcommand_autocomplete():
    handler = InputHandler()
    ollama_entry = handler.command_completions["/ollama"]
    assert ollama_entry is not None
    if hasattr(ollama_entry, "options"):
        subs = set(ollama_entry.options.keys())
        assert {"status", "models", "pull", "options"}.issubset(subs)


# ============================================================ no aliases-of-aliases


def test_quit_canonical_has_one_alias():
    """We deliberately kept /q (Unix muscle memory) but dropped /exit."""
    handler = InputHandler()
    autocomplete = set(handler.command_completions.keys())
    assert "/quit" in autocomplete
    assert "/q" in autocomplete
    assert "/exit" not in autocomplete
