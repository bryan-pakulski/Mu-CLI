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
import shlex

from mu.tools.capabilities import normalize_session_type
from mu.container.load_errors import describe_container_load_error, format_container_load_error

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
    if normalize_session_type(session.variables.get("session_type")) == "container":
        from mu.container.tui import ensure_tui_container

        ensure_tui_container(session)

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
                session.variables.get("ollama_mode"),
                session.variables.get("ollama_api_key") or None,
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

    _queue_session_resumption_briefing(session)

    msg = f"Loaded session: {session.session_manager.current_session_name}"
    _emit(session, msg, allow_prompt)
    return CommandResult(ok=True, message=msg)


def _queue_session_resumption_briefing(session: Any) -> None:
    """If the loaded session has active teacher / feature state, brief
    the next-turn agent so it picks up where the previous session left
    off without asking the user to re-explain."""
    if not hasattr(session, "queue_resumption_briefing"):
        return
    sm = session.session_manager
    name = sm.current_session_name
    fragments = [f"You just loaded session **{name}**."]

    teacher_state = sm.get_teacher_state() if hasattr(sm, "get_teacher_state") else None
    if isinstance(teacher_state, dict):
        course_id = teacher_state.get("course_id") or ""
        subject = teacher_state.get("subject") or ""
        status = teacher_state.get("status") or ""
        metrics = teacher_state.get("metrics") or {}
        fragments.append(
            f"ACTIVE COURSE: `{course_id}` (subject: {subject!r}, status: "
            f"{status}, progress: {metrics.get('lessons_completed', 0)}/"
            f"{metrics.get('total_lessons', 0)} lessons, avg "
            f"{metrics.get('average_score_pct', 0)}%). Run /teach status if "
            "you need a finer-grained snapshot before resuming."
        )

    feature_state = sm.get_feature_state() if hasattr(sm, "get_feature_state") else None
    if isinstance(feature_state, dict):
        feature_id = feature_state.get("feature_id") or ""
        feature_name = feature_state.get("feature_name") or ""
        status = feature_state.get("status") or ""
        fragments.append(
            f"ACTIVE FEATURE: `{feature_id}` (name: {feature_name!r}, "
            f"status: {status}). Call get_current_task to see the next "
            "actionable item; do NOT re-plan from scratch."
        )

    if len(fragments) <= 1:
        return  # only the session-name line; not worth briefing
    session.queue_resumption_briefing("\n".join(fragments))


def _parse_new_args(raw: str) -> tuple[str | None, str]:
    parts = shlex.split(raw or "")
    session_type = "workspace"
    name: str | None = None
    index = 0
    while index < len(parts):
        part = parts[index]
        if part == "--type" and index + 1 < len(parts):
            session_type = normalize_session_type(parts[index + 1])
            index += 2
            continue
        if part.startswith("--type="):
            session_type = normalize_session_type(part.split("=", 1)[1])
            index += 1
            continue
        if name is None:
            name = part
        index += 1
    return name, session_type


def _new_session(session: Any, raw: str, allow_prompt: bool) -> CommandResult:
    target_name, session_type = _parse_new_args(raw)
    # Container sessions are created through the same command in the TUI and
    # walk through container selection, Dockerfile, mounts, and network policy.

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
                # No mode deliberately: the picker asks local vs cloud for
                # each newly-created Ollama session.
                ollama_mode=None,
                ollama_api_key=session.variables.get("ollama_api_key") or None,
                allow_prompt=allow_prompt,
            )
            session.provider = new_provider
        except ImportError:
            # Tests without mucli: reuse the existing provider.
            pass

    selected_ollama_mode = getattr(session.provider, "_mu_ollama_mode", None)
    session.session_manager.new_session(
        target_name,
        session.provider.name,
        session.provider.model_name,
        session_type=session_type,
    )
    if session.provider.name == "ollama" and selected_ollama_mode:
        session.variables["ollama_mode"] = selected_ollama_mode
        if selected_ollama_mode == "local":
            session.variables["ollama_host"] = ""
        session.session_manager.save_history(session.folder_context)
    session.staged_files = []
    session.sync_runtime_state()
    if session_type == "container":
        from mu.container.tui import configure_tui_container, ensure_tui_container

        if allow_prompt:
            configure_tui_container(session)
        ensure_tui_container(session)

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

    msg = (
        f"Started new session: {session.session_manager.current_session_name} "
        f"(type={session_type})"
    )
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
    help="Manage saved sessions: list, load <name>, new [--type chat|workspace|container] [name], delete <name>.",
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
        try:
            return _load_session(session, rest, allow_prompt)
        except Exception as exc:
            failure = describe_container_load_error(
                exc,
                session_name=rest,
                container_name=str((getattr(session.session_manager, "container_config", {}) or {}).get("container_name") or ""),
            )
            message = format_container_load_error(failure)
            _emit_error(session, message, allow_prompt)
            return CommandResult(ok=False, message=message, data={"error": failure})
    if sub == "new":
        return _new_session(session, rest, allow_prompt)
    if sub == "delete":
        return _delete_session(session, rest, allow_prompt)

    return CommandResult(
        ok=False,
        message=f"Unknown subcommand {sub!r}. Usage: /session [list|load <n>|new [n]|delete <n>]",
    )
