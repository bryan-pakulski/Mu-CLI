"""Slash command for saved-session management.

Replaces the earlier `/list`, `/load`, `/new`, `/delete` commands. The
sub-commands group related operations:

    /session              — show active session + available sessions
    /session list         — list saved sessions
    /session load <name>  — switch to an existing session
    /session new [name]   — start a new session (interactive if a UI is attached)
    /session delete <name>— delete a saved session
"""

from typing import Any

from . import CommandResult, command


def _emit(session: Any, body: str, allow_prompt: bool) -> None:
    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "show_info") and allow_prompt:
        ui.show_info(body)


def _emit_error(session: Any, body: str, allow_prompt: bool) -> None:
    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "show_error") and allow_prompt:
        ui.show_error(body)


def _list_sessions(session: Any, allow_prompt: bool) -> CommandResult:
    if allow_prompt:
        session.session_manager.list_sessions()
    return CommandResult(
        ok=True,
        message=f"{len(session.session_manager.get_session_list())} saved session(s).",
        data={
            "sessions": session.session_manager.get_session_list(),
            "active": session.session_manager.current_session_name,
        },
    )


def _load_session(session: Any, name: str, allow_prompt: bool) -> CommandResult:
    if not name:
        msg = "Usage: /session load <name>"
        _emit_error(session, msg, allow_prompt)
        return CommandResult(ok=False, message=msg)

    session.session_manager.switch_session(name)
    session.staged_files = []
    session.sync_runtime_state()

    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "set_variables"):
        ui.set_variables(session.variables)

    provider_config = session.session_manager.provider_config
    if provider_config.get("provider") and provider_config.get("model"):
        try:
            from mucli import init_provider, sync_provider_settings

            ollama_host = session.variables.get("ollama_host")
            session.provider = init_provider(
                provider_config["provider"],
                provider_config["model"],
                ollama_host,
            )
            sync_provider_settings(session)
        except ImportError:
            # mucli isn't importable in unit-test contexts — that's fine,
            # the session loaded but provider didn't re-init.
            pass

    if allow_prompt:
        try:
            from mucli import print_splash, refresh_memory_hud

            print_splash(session)
            refresh_memory_hud(session, ui)
        except ImportError:
            pass

    msg = f"Loaded session: {session.session_manager.current_session_name}"
    _emit(session, msg, allow_prompt)
    return CommandResult(ok=True, message=msg)


def _new_session(session: Any, name: str, allow_prompt: bool) -> CommandResult:
    target_name = name.strip() if name else None

    has_provider = bool(
        getattr(session.provider, "name", None)
        and getattr(session.provider, "model_name", None)
    )
    if not allow_prompt and not has_provider:
        return CommandResult(
            ok=False,
            message="Non-interactive mode requires an active provider/model to create a new session.",
        )

    if allow_prompt:
        try:
            from mucli import select_provider_and_model

            ollama_host = session.variables.get("ollama_host")
            new_provider = select_provider_and_model(
                None,
                None,
                ollama_host=ollama_host,
                allow_prompt=allow_prompt,
            )
            session.provider = new_provider
        except ImportError:
            # Tests without mucli: reuse the existing provider.
            pass

    session.session_manager.new_session(
        target_name,
        session.provider.name,
        session.provider.model_name,
    )
    session.staged_files = []
    session.sync_runtime_state()

    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "set_variables"):
        ui.set_variables(session.variables)

    if allow_prompt:
        try:
            from mucli import print_splash, refresh_memory_hud

            print_splash(session)
            refresh_memory_hud(session, ui)
        except ImportError:
            pass

    msg = f"Started new session: {session.session_manager.current_session_name}"
    return CommandResult(ok=True, message=msg)


def _delete_session(session: Any, name: str, allow_prompt: bool) -> CommandResult:
    if not name:
        msg = "Usage: /session delete <name>"
        _emit_error(session, msg, allow_prompt)
        return CommandResult(ok=False, message=msg)

    session.session_manager.delete_session(name.strip())
    msg = f"Deleted session: {name.strip()}"
    _emit(session, msg, allow_prompt)
    return CommandResult(ok=True, message=msg, data={"deleted": name.strip()})


@command(
    "/session",
    help="Manage saved sessions: list, load <name>, new [name], delete <name>.",
)
def session_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()
    if not raw:
        return _list_sessions(session, allow_prompt)

    head, _, rest = raw.partition(" ")
    sub = head.lower()
    rest = rest.strip()

    if sub == "list":
        return _list_sessions(session, allow_prompt)
    if sub == "load":
        return _load_session(session, rest, allow_prompt)
    if sub == "new":
        return _new_session(session, rest, allow_prompt)
    if sub == "delete":
        return _delete_session(session, rest, allow_prompt)

    return CommandResult(
        ok=False,
        message=f"Unknown subcommand {sub!r}. Usage: /session [list|load <n>|new [n]|delete <n>]",
    )
