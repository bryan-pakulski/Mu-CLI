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


def _legacy_dispatcher_commands() -> set:
    """Scrape `mucli.handle_command` for `cmd == "/foo"` and `cmd in [...]`
    branches. We use regex on the source because the dispatcher is one big
    if-elif chain; parsing it any other way is wishful thinking."""
    import inspect
    import mucli

    src = inspect.getsource(mucli.handle_command)
    commands: set = set()
    # cmd == "/foo"
    for match in re.finditer(r'cmd\s*==\s*"(\/[^"]+)"', src):
        commands.add(match.group(1))
    # cmd in ["/a", "/b"]  (and parenthesized form)
    for match in re.finditer(r'cmd\s+in\s+[\[\(]([^)\]]+)[\]\)]', src):
        for item in match.group(1).split(","):
            item = item.strip().strip('"').strip("'")
            if item.startswith("/"):
                commands.add(item)
    return commands


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


def test_autocomplete_has_no_dead_entries():
    """Every entry in autocomplete must actually dispatch somewhere."""
    autocomplete = _autocomplete_commands()
    new_registry = _new_registry_aliases()
    legacy = _legacy_dispatcher_commands()

    dispatched = new_registry | legacy
    dead = autocomplete - dispatched - _INTENTIONALLY_HIDDEN
    assert not dead, (
        f"Autocomplete advertises commands that nothing dispatches: {sorted(dead)}. "
        "Either remove them from `InputHandler.command_completions` or add a "
        "dispatcher branch."
    )


def test_every_dispatched_command_is_autocompletable():
    """Every command users can actually run should be Tab-completable."""
    autocomplete = _autocomplete_commands()
    new_registry = _new_registry_aliases()
    legacy = _legacy_dispatcher_commands()

    # Filter out empty / non-slash captures from the regex pass.
    dispatched = {c for c in (new_registry | legacy) if c.startswith("/")}
    missing = dispatched - autocomplete - _INTENTIONALLY_HIDDEN
    assert not missing, (
        f"These commands dispatch but have no autocomplete entry: {sorted(missing)}."
    )


# ============================================================ /help mirrors autocomplete


def test_help_only_lists_real_commands():
    """Every command in /help must be actually dispatchable."""
    help_cmds = _help_commands()
    new_registry = _new_registry_aliases()
    legacy = _legacy_dispatcher_commands()
    dispatched = new_registry | legacy
    missing = help_cmds - dispatched
    assert not missing, (
        f"/help lists commands that no dispatcher handles: {sorted(missing)}"
    )


_DROPPED_ALIASES = {
    "/exit",
    # NOTE: /h was previously in this set but was reinstated as a /help
    # alias — README advertises it and users hit it reflexively.
    "/c",
    "/v",
    "/dir",
    "/sys",
    "/ls",
    "/rm",
    "/open",
    "/add",
    "/f",
    "/cf",
    "/cw",
    "/clear-workspace",
    "/features",
    "/tools",
    "/splash",
    "/update",
}


def test_help_groups_have_no_dropped_aliases():
    """We deliberately removed these aliases. None should still appear in /help."""
    help_cmds = _help_commands()
    leaked = _DROPPED_ALIASES & help_cmds
    assert not leaked, f"Dropped aliases still in /help: {sorted(leaked)}"


def test_autocomplete_has_no_dropped_aliases():
    """And none should be tab-completable either."""
    autocomplete = _autocomplete_commands()
    leaked = _DROPPED_ALIASES & autocomplete
    assert not leaked, (
        f"Dropped aliases still in InputHandler.command_completions: {sorted(leaked)}"
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
        "/mcp",
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
