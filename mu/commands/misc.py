"""Miscellaneous slash commands: /help, /quit, /clear, /history."""

from typing import Any

from . import CommandResult, command


@command("/quit", "/q", help="Exit the REPL.")
def quit_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    return CommandResult(ok=True, message="Goodbye!", data={"exit": True}, exit=True)


@command(
    "/clear",
    help="Clear the terminal screen (does NOT touch history — use /history clear for that).",
)
def clear_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    if allow_prompt:
        ui = getattr(session, "ui", None)
        console = getattr(ui, "console", None) if ui is not None else None
        if console is not None:
            try:
                console.clear()
            except Exception:
                pass
    return CommandResult(ok=True, message="Screen cleared.")


def _history_search_subcommand(session: Any, query: str, *, allow_prompt: bool) -> CommandResult:
    """Handle `/history search <query>` — search conversation log."""
    query = (query or "").strip()
    if not query:
        return CommandResult(
            ok=False,
            message="Usage: /history search <query> [--role <role>] [--tool <name>] [--limit N]",
        )

    # Parse optional flags from the query string
    role = None
    tool_name = None
    max_results = 20
    parts = query.split()
    clean_parts = []
    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok == "--role" and i + 1 < len(parts):
            role = parts[i + 1]
            i += 2
        elif tok == "--tool" and i + 1 < len(parts):
            tool_name = parts[i + 1]
            i += 2
        elif tok == "--limit" and i + 1 < len(parts):
            try:
                max_results = int(parts[i + 1])
            except ValueError:
                pass
            i += 2
        else:
            clean_parts.append(tok)
            i += 1
    query = " ".join(clean_parts).strip()
    if not query:
        return CommandResult(
            ok=False,
            message="Usage: /history search <query> [--role <role>] [--tool <name>] [--limit N]",
        )

    results = session.session_manager.search_history(
        query=query,
        role=role,
        tool_name=tool_name,
        max_results=max_results,
    )

    total = results.get("total_matches", 0)
    hits = results.get("results", [])
    if not hits:
        msg = f"No matches found for {query!r}"
        if allow_prompt:
            ui = getattr(session, "ui", None)
            if ui and hasattr(ui, "show_info"):
                ui.show_info(msg)
        return CommandResult(ok=True, message=msg, data=results)

    # Build readable output
    lines = [f"Found {total} match(es) for {query!r}:"]
    for hit in hits:
        idx = hit.get("index", "?")
        role_str = hit.get("role", "?")
        before_anchor = hit.get("before_anchor", False)
        anchor_tag = " [compacted]" if before_anchor else ""
        lines.append(f"\n  [{idx}] {role_str}{anchor_tag}:")
        for pm in hit.get("parts_matched", []):
            snippet = pm.get("snippet", "")
            match_type = pm.get("match_type", "text")
            cache_key = pm.get("cache_key")
            line = f"    ({match_type}) {snippet}"
            if cache_key:
                line += f"  [cache:{cache_key}]"
            lines.append(line)
        # Context before
        for ctx in hit.get("context_before", []):
            cidx = ctx.get("index", "?")
            crole = ctx.get("role", "?")
            cprev = ctx.get("preview", "")[:80]
            lines.append(f"    ↑ [{cidx}] {crole}: {cprev}")
        # Context after
        for ctx in hit.get("context_after", []):
            cidx = ctx.get("index", "?")
            crole = ctx.get("role", "?")
            cprev = ctx.get("preview", "")[:80]
            lines.append(f"    ↓ [{cidx}] {crole}: {cprev}")

    if results.get("has_more"):
        lines.append(f"\n  ... and {total - len(hits)} more. Use --limit or narrower filters.")

    msg = "\n".join(lines)
    if allow_prompt:
        ui = getattr(session, "ui", None)
        if ui and hasattr(ui, "show_info"):
            ui.show_info(msg)

    return CommandResult(ok=True, message=msg, data=results)


@command(
    "/history",
    help="Show conversation history; /history clear wipes it; /history search <query> searches.",
)
def history_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()
    sub_lower = raw.lower()

    # Subcommand routing
    if sub_lower == "clear":
        session.session_manager.clear_current_history()
        return CommandResult(ok=True, message="Conversation history cleared.")

    if sub_lower.startswith("search ") or sub_lower == "search":
        # Extract the query part after "search "
        search_query = raw[6:].strip() if len(raw) > 6 else ""
        return _history_search_subcommand(
            session, search_query, allow_prompt=allow_prompt
        )

    if sub_lower and sub_lower != "show":
        return CommandResult(
            ok=False,
            message=f"Unknown subcommand {sub_lower!r}. Usage: /history [show|clear|search <query>]",
        )

    if allow_prompt:
        session.session_manager.view_history()
    return CommandResult(
        ok=True,
        data={"history": session.session_manager.history},
    )


def _help_groups() -> list:
    """Pull the canonical help table from mucli, falling back to whatever
    is currently in the registry if mucli isn't importable (test envs)."""
    try:
        from mucli import _HELP_GROUPS

        return list(_HELP_GROUPS)
    except Exception:
        # Build a minimal grouping from the registry so tests / partial
        # environments still get useful output.
        from . import list_commands

        rows = []
        seen = set()
        for spec in list_commands():
            key = spec.names[0]
            if key in seen:
                continue
            seen.add(key)
            aliases = "/".join(spec.names[1:]) if len(spec.names) > 1 else ""
            rows.append((spec.names[0], aliases, spec.help))
        return [("Available commands", rows)]


@command("/help", "/h", help="Show this menu of slash commands.")
def help_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    groups = _help_groups()

    # Interactive: render the Rich tables via mucli.print_help so the
    # user sees the same view they always have.
    if allow_prompt:
        try:
            from mucli import print_help

            print_help()
        except Exception:
            # Fall back to plain text if mucli isn't importable.
            for name, rows in groups:
                print(name)
                for cmd, alias, desc in rows:
                    alias_str = f" ({alias})" if alias else ""
                    print(f"  {cmd}{alias_str} — {desc}")

    # Always populate `message` and `data` so non-interactive callers
    # (JSON output, tests) get the full surface.
    lines = []
    flat = []
    for name, rows in groups:
        lines.append(f"\n{name}:")
        for cmd, alias, desc in rows:
            alias_str = f" ({alias})" if alias else ""
            lines.append(f"  {cmd}{alias_str} — {desc}")
            flat.append({"command": cmd, "alias": alias, "description": desc, "group": name})

    body = "\n".join(lines).strip()
    return CommandResult(
        ok=True,
        message=body,
        data={"commands_help": True, "groups": groups, "commands": flat},
    )
