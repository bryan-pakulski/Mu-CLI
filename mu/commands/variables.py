"""Session-variable slash commands: /set, /get, /unset, /variables.

`/set layer <id> <chars>` and `/get layer [<id>]` are ergonomic
shortcuts over the underlying per-layer char-budget variables — short
layer IDs (L1, L1B, L2, ...) are easier to remember than
`workspace_context_max_chars` and friends.
"""

from typing import Any, Dict, Tuple

from . import CommandResult, command


# The underlying per-layer variables are budgets in CHARS (text is
# truncated to that many chars before being tokenized for the prompt).
# But `/memory`, the splash banner, and everything else surfaces token
# counts — that's the unit users see. So the `/set layer <id> <value>`
# shortcut accepts TOKENS for UX consistency and converts to chars
# under the hood at this ratio. Matches the conservative chars/4
# heuristic used in `_budget_chars_to_tokens` (runtime_metrics.py) so
# the displayed `maximum` round-trips correctly.
TOKEN_TO_CHAR_RATIO = 4


# Layer ID → (variable name, human label, description).
# L5 (conversation history) is intentionally absent: it has no per-layer
# budget. It gets whatever room the global cap minus the response
# reserve minus all the non-L5 layers leaves over. Tighten it via
# `context_token_limit` instead.
LAYER_BUDGET_VARS: Dict[str, Tuple[str, str, str]] = {
    "L1": (
        "workspace_context_max_chars",
        "Workspace files",
        "AGENTS.md / CLAUDE.md / .mu/CONTEXT.md per attached folder",
    ),
    "L1B": (
        "skills_max_chars",
        "Installed skills",
        "AVAILABLE SKILLS block (compact index + auto-expanded bodies)",
    ),
    "L2": (
        "conversation_summary_char_limit",
        "Conversation summary",
        "Rolling summary of older history",
    ),
    "L3": (
        "active_goal_context_char_limit",
        "Active goal",
        "Feature/task status + scratchpad snapshot",
    ),
    "L4": (
        "recent_tool_context_char_limit",
        "Recent tool activity",
        "Compressed recent tool calls/results",
    ),
    "L4B": (
        "retrieval_context_char_limit",
        "Retrieved snippets",
        "Semantic-retrieval context injected for the current turn",
    ),
}


def _emit(session: Any, body: str, allow_prompt: bool, *, error: bool = False) -> None:
    ui = getattr(session, "ui", None)
    if ui is None or not allow_prompt:
        return
    method = "show_error" if error else "show_info"
    if hasattr(ui, method):
        getattr(ui, method)(body)


def _refresh_hud(session: Any) -> None:
    try:
        from mucli import refresh_memory_hud

        refresh_memory_hud(session, getattr(session, "ui", None))
    except ImportError:
        pass


def _sync_provider_if_needed(session: Any, key: str) -> None:
    if key == "ollama_host":
        try:
            from mucli import sync_provider_settings

            sync_provider_settings(session)
        except ImportError:
            pass


def _persist(session: Any) -> None:
    fc = getattr(session, "folder_context", None)
    session.session_manager.save_history(fc)


def _resolve_layer(token: str):
    """Map a user-provided layer ID (case-insensitive) to its config row,
    or None if unknown."""
    return LAYER_BUDGET_VARS.get(token.strip().upper())


def _list_layer_budgets(session: Any, allow_prompt: bool) -> CommandResult:
    """Print every layer's current budget in both tokens (displayed
    elsewhere in the UI) and the underlying char-budget."""
    rows = []
    for layer_id, (var_name, label, desc) in LAYER_BUDGET_VARS.items():
        current_chars = session.variables.get(var_name)
        try:
            current_tokens = max(1, int(current_chars) // TOKEN_TO_CHAR_RATIO)
        except (TypeError, ValueError):
            current_tokens = None
        rows.append(
            {
                "layer": layer_id,
                "variable": var_name,
                "tokens": current_tokens,
                "chars": current_chars,
                "label": label,
                "description": desc,
            }
        )

    if allow_prompt:
        ui = getattr(session, "ui", None)
        console = getattr(ui, "console", None) if ui is not None else None
        if console is not None:
            try:
                from rich import box
                from rich.table import Table

                table = Table(title="Layer budgets", box=box.SIMPLE)
                table.add_column("Layer", style="cyan", no_wrap=True)
                table.add_column("Tokens", style="green", justify="right")
                table.add_column("Chars", style="dim", justify="right")
                table.add_column("Variable", style="dim")
                table.add_column("Description", style="white")
                for row in rows:
                    table.add_row(
                        row["layer"],
                        str(row["tokens"]) if row["tokens"] is not None else "-",
                        str(row["chars"]),
                        row["variable"],
                        row["description"],
                    )
                console.print(table)
                console.print(
                    "[dim]Set with[/dim] [bold]/set layer <id> <tokens>[/bold]"
                    " — e.g. /set layer L4 6000\n"
                    "[dim]L5 has no per-layer budget; tighten[/dim] "
                    "[bold]context_token_limit[/bold] [dim]instead.[/dim]"
                )
            except Exception:
                pass

    return CommandResult(
        ok=True,
        message=f"{len(rows)} layer budgets.",
        data={"layer_budgets": rows},
    )


def _set_layer_budget(
    session: Any, raw_args: str, allow_prompt: bool
) -> CommandResult:
    """`/set layer <id> <tokens>`. Value is in TOKENS — same unit shown
    in `/memory` and the splash banner — and converted to chars
    internally because the underlying truncation is char-based."""
    parts = raw_args.split(None, 1)
    if len(parts) < 2:
        msg = "Usage: /set layer <id> <tokens>  (e.g. /set layer L4 6000)"
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)

    layer_id_raw, value_raw = parts[0], parts[1].strip()
    layer_upper = layer_id_raw.strip().upper()
    if layer_upper == "L0":
        msg = (
            "L0 (system prompt) has no char budget — it's set by --system "
            "at startup or by editing session.system_instruction directly. "
            "View its current content with /memory list L0."
        )
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)
    if layer_upper == "L5":
        msg = (
            "L5 (conversation history) has no per-layer budget — it gets the "
            "remainder of context_token_limit after the other layers. Tighten "
            "context_token_limit instead: /set context_token_limit <tokens>"
        )
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)

    row = _resolve_layer(layer_id_raw)
    if row is None:
        valid = ", ".join(LAYER_BUDGET_VARS.keys())
        msg = f"Unknown layer {layer_id_raw!r}. Valid: {valid}"
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)

    try:
        tokens = int(value_raw)
    except ValueError:
        msg = f"Invalid token count {value_raw!r} — must be a positive integer."
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)
    if tokens <= 0:
        msg = f"Token count must be > 0 (got {tokens})."
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)

    char_value = tokens * TOKEN_TO_CHAR_RATIO
    var_name, label, _desc = row
    try:
        from utils.config import validate_and_cast

        session.variables[var_name] = validate_and_cast(var_name, char_value)
    except ValueError as exc:
        _emit(session, f"Error: {exc}", allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc))

    _persist(session)
    _refresh_hud(session)
    chars_set = session.variables[var_name]
    layer_id = layer_id_raw.strip().upper()
    _emit(
        session,
        f"Set {layer_id} ({label}) = {tokens} tokens "
        f"({var_name} = {chars_set} chars)",
        allow_prompt,
    )
    return CommandResult(
        ok=True,
        message=(
            f"Set {layer_id} ({var_name}) = {tokens} tokens / {chars_set} chars"
        ),
        data={
            "layer": layer_id,
            "variable": var_name,
            "tokens": tokens,
            "chars": chars_set,
        },
    )


def _get_layer_budget(
    session: Any, raw_args: str, allow_prompt: bool
) -> CommandResult:
    target = raw_args.strip()
    if not target:
        return _list_layer_budgets(session, allow_prompt)

    if target.upper() == "L0":
        msg = (
            "L0 (system prompt) has no char budget — set via --system at "
            "startup. View current content with /memory list L0."
        )
        _emit(session, msg, allow_prompt)
        return CommandResult(ok=True, message=msg, data={"layer": "L0"})
    if target.upper() == "L5":
        msg = (
            "L5 has no per-layer budget. Its effective ceiling is "
            "context_token_limit minus the non-L5 layers and the "
            "response reserve. See /memory for the live numbers."
        )
        _emit(session, msg, allow_prompt)
        return CommandResult(ok=True, message=msg, data={"layer": "L5"})

    row = _resolve_layer(target)
    if row is None:
        valid = ", ".join(LAYER_BUDGET_VARS.keys())
        msg = f"Unknown layer {target!r}. Valid: {valid}"
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)

    var_name, label, desc = row
    current_chars = session.variables.get(var_name)
    try:
        current_tokens = max(1, int(current_chars) // TOKEN_TO_CHAR_RATIO)
    except (TypeError, ValueError):
        current_tokens = None
    layer_id = target.upper()
    msg = (
        f"{layer_id} ({label}) — {current_tokens} tokens "
        f"({var_name} = {current_chars} chars)"
    )
    _emit(session, msg, allow_prompt)
    return CommandResult(
        ok=True,
        message=msg,
        data={
            "layer": layer_id,
            "variable": var_name,
            "tokens": current_tokens,
            "chars": current_chars,
            "description": desc,
        },
    )


@command(
    "/set",
    help=(
        "Set a session variable: /set <key> <value> or /set <key>=<value>. "
        "Layer budgets: /set layer <id> <tokens> (matches the unit shown in /memory)."
    ),
)
def set_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    if not args:
        msg = "Usage: /set <key> <value>  (or /set layer <id> <tokens>)"
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)

    head, _, rest = args.partition(" ")
    if head.lower() == "layer":
        return _set_layer_budget(session, rest, allow_prompt)

    if "=" in args:
        key, value = args.split("=", 1)
    elif " " in args:
        key, value = args.split(" ", 1)
    else:
        msg = "Usage: /set <key> <value> OR /set <key>=<value>"
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)

    key = key.strip()
    value = value.strip()
    try:
        from utils.config import validate_and_cast

        session.variables[key] = validate_and_cast(key, value)
    except ValueError as exc:
        _emit(session, f"Error: {exc}", allow_prompt, error=True)
        return CommandResult(ok=False, message=str(exc))

    _persist(session)
    _sync_provider_if_needed(session, key)
    _refresh_hud(session)
    casted = session.variables[key]
    _emit(
        session,
        f"Set variable: {key} = {casted} ({type(casted).__name__})",
        allow_prompt,
    )
    return CommandResult(
        ok=True,
        message=f"Set variable: {key}",
        data={"key": key, "value": casted},
    )


@command(
    "/get",
    help=(
        "Get one session variable, or list all if no key given. "
        "Layer budgets: /get layer [<id>]."
    ),
)
def get_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    key = (args or "").strip()

    if key.lower().startswith("layer"):
        rest = key[5:].strip() if len(key) > 5 else ""
        return _get_layer_budget(session, rest, allow_prompt)

    if not key:
        if allow_prompt:
            ui = getattr(session, "ui", None)
            console = getattr(ui, "console", None) if ui is not None else None
            if console is not None:
                from utils.helpers import safe_markup

                for k, v in session.variables.items():
                    console.print(f"[blue]{safe_markup(k)}[/blue] = {safe_markup(v)}")
        return CommandResult(
            ok=True,
            message="Listed variables.",
            data={"variables": dict(session.variables)},
        )
    return CommandResult(
        ok=True,
        message=f"{key} = {session.variables.get(key)}",
        data={"key": key, "value": session.variables.get(key)},
    )


@command("/unset", help="Reset a variable to its default. /unset --all resets all.")
def unset_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    key = (args or "").strip()
    if not key:
        msg = "Usage: /unset <key> OR /unset --all"
        _emit(session, msg, allow_prompt, error=True)
        return CommandResult(ok=False, message=msg)

    if key == "--all":
        from utils.config import DEFAULT_VARIABLES

        session.variables.clear()
        session.variables.update(DEFAULT_VARIABLES)
        _persist(session)
        _sync_provider_if_needed(session, "ollama_host")  # always re-sync
        _refresh_hud(session)
        return CommandResult(ok=True, message="All variables reset to defaults.")

    if key not in session.variables:
        return CommandResult(ok=False, message=f"Variable {key!r} not found.")

    from utils.config import VARIABLE_SCHEMA

    if key in VARIABLE_SCHEMA:
        session.variables[key] = VARIABLE_SCHEMA[key]["default"]
    else:
        del session.variables[key]
    _persist(session)
    _sync_provider_if_needed(session, key)
    _refresh_hud(session)
    return CommandResult(
        ok=True,
        message=f"Unset variable: {key}",
        data={"key": key, "value": session.variables.get(key)},
    )


@command("/variables", help="Show every session variable and its current value.")
def variables_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    if allow_prompt:
        ui = getattr(session, "ui", None)
        console = getattr(ui, "console", None) if ui is not None else None
        if console is not None:
            from utils.helpers import safe_markup

            for k, v in session.variables.items():
                console.print(
                    f"[blue]{safe_markup(k)}[/blue] = [green]{safe_markup(v)}[/green]"
                )
    return CommandResult(
        ok=True,
        message="Listed variables.",
        data={"variables": dict(session.variables)},
    )
