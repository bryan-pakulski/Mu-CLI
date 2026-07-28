"""Interactive terminal setup and synchronous bridge for container sessions.

The browser/mobile GUI receives worker events through the host FastAPI bridge.
The TUI has no callback server, so it creates/attaches the worker with an empty
supervisor URL and uses the worker's synchronous turn endpoint.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt

from .builder import default_dockerfile
from .network import DEFAULT_EGRESS_ALLOW
from .supervisor import ContainerSupervisor

console = Console()


def _domain_list(value: Any, fallback: list[str] | None = None) -> list[str]:
    if isinstance(value, str):
        raw = value.replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = fallback or []
    return list(
        dict.fromkeys(
            str(item or "").strip().lower()
            for item in raw
            if str(item or "").strip()
        )
    )


def _prompt_domain_list(label: str, current: list[str]) -> list[str]:
    value = Prompt.ask(
        label,
        default=", ".join(current),
        show_default=bool(current),
    )
    return _domain_list(value)


def _edit_dockerfile(initial: str) -> str:
    """Open the template in $VISUAL/$EDITOR, or accept a file path fallback."""
    editor = str(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "").strip()
    if editor:
        fd, path = tempfile.mkstemp(prefix="mucli-Dockerfile-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(initial)
            command = [*shlex.split(editor), path]
            result = subprocess.run(command, check=False)
            if result.returncode != 0:
                console.print(
                    f"[yellow]editor exited with status {result.returncode}; keeping the previous template[/yellow]"
                )
                return initial
            return Path(path).read_text(encoding="utf-8")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    console.print(
        "[dim]Set $EDITOR or $VISUAL to edit inline. You can also load a Dockerfile from disk.[/dim]"
    )
    source = Prompt.ask("Dockerfile path (blank keeps template)", default="").strip()
    if not source:
        return initial
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        console.print(f"[yellow]not a file: {path}; keeping the template[/yellow]")
        return initial
    return path.read_text(encoding="utf-8")


def _prompt_mounts(current: list[dict[str, str]]) -> list[dict[str, str]]:
    mounts = [dict(item) for item in current]
    if mounts:
        console.print("[dim]Current bind mounts:[/dim]")
        for item in mounts:
            console.print(
                f"  {item.get('host_path')} → {item.get('container_path')} ({item.get('mode', 'rw')})",
                markup=False,
            )
        action = Prompt.ask(
            "Bind mounts",
            choices=["keep", "add", "clear"],
            default="keep",
        )
        if action == "keep":
            return mounts
        if action == "clear":
            mounts = []
    elif not Confirm.ask("Add a host bind mount", default=False):
        return mounts

    while True:
        host_path = Prompt.ask("Host path (blank finishes)", default="").strip()
        if not host_path:
            break
        host_path = str(Path(host_path).expanduser().resolve())
        container_path = Prompt.ask(
            "Container path",
            default=f"/workspace/{Path(host_path).name or 'project'}",
        ).strip()
        mode = Prompt.ask("Mount mode", choices=["rw", "ro"], default="rw")
        mounts.append(
            {
                "host_path": host_path,
                "container_path": container_path,
                "mode": mode,
            }
        )
        if not Confirm.ask("Add another bind mount", default=False):
            break
    return mounts


def _select_existing_container(supervisor: ContainerSupervisor):
    refs = supervisor.registry.list_containers()
    if not refs:
        return None
    console.print("\n[bold cyan]Managed containers:[/bold cyan]")
    for index, ref in enumerate(refs, 1):
        sessions = ", ".join(ref.attached_sessions) or "none"
        console.print(
            f" {index}. {ref.name}  [{ref.status}]  sessions: {sessions}",
            markup=False,
        )
    choice = IntPrompt.ask(
        "Select a container",
        choices=[str(index) for index in range(1, len(refs) + 1)],
    )
    return refs[int(choice) - 1]


def configure_tui_container(
    session: Any,
    *,
    supervisor: ContainerSupervisor | None = None,
) -> dict[str, Any]:
    """Prompt through container selection and editable creation stages."""
    manager = session.session_manager
    name = manager.current_session_name
    config = dict(getattr(manager, "container_config", {}) or {})
    supervisor = supervisor or getattr(session, "_container_supervisor", None) or ContainerSupervisor()
    session._container_supervisor = supervisor

    refs = supervisor.registry.list_containers()
    console.print("\n[bold cyan]Container setup[/bold cyan]")
    if refs:
        source = Prompt.ask(
            "Container source",
            choices=["configure", "existing"],
            default="configure",
        )
    else:
        source = "configure"
        console.print("[dim]No managed containers are available; configure a new worker.[/dim]")

    if source == "existing":
        ref = _select_existing_container(supervisor)
        if ref is None:
            source = "configure"
        else:
            config = {
                "container_name": ref.name,
                "dockerfile": None,
                "template_name": ref.template_name or None,
                "mounts": [mount.to_dict() for mount in ref.mounts],
                "egress_allow": list(ref.egress_allow),
                "egress_deny": list(ref.egress_deny),
            }
            console.print(f"[dim]selected → {ref.name}[/dim]", markup=False)

    if source == "configure":
        default_name = str(config.get("container_name") or f"mucli-{name}").strip()
        config["container_name"] = Prompt.ask("Container name", default=default_name).strip()

        dockerfile = str(config.get("dockerfile") or default_dockerfile())
        if Confirm.ask("Edit the Dockerfile template", default=False):
            dockerfile = _edit_dockerfile(dockerfile)
        config["dockerfile"] = dockerfile

        config["mounts"] = _prompt_mounts(list(config.get("mounts") or []))

        allow = _domain_list(config.get("egress_allow"), DEFAULT_EGRESS_ALLOW)
        console.print(f"[dim]egress allowlist → {', '.join(allow) or 'empty'}[/dim]")
        if Confirm.ask("Edit the egress allowlist", default=False):
            allow = _prompt_domain_list("Allowed domains (comma-separated)", allow)
        config["egress_allow"] = allow

        deny = _domain_list(config.get("egress_deny"), [])
        console.print(f"[dim]egress blocklist → {', '.join(deny) or 'empty'}[/dim]")
        if Confirm.ask("Edit the egress blocklist", default=False):
            deny = _prompt_domain_list("Blocked domains (comma-separated)", deny)
        config["egress_deny"] = deny

    manager.container_config = config
    manager.save_history(session.folder_context)
    return config


def _progress(stage: str, message: str) -> None:
    label = stage.replace("_", " ")
    console.print(f"[cyan]container · {label}[/cyan]  {message}")


def ensure_tui_container(session: Any) -> Any:
    manager = session.session_manager
    name = manager.current_session_name
    config = dict(getattr(manager, "container_config", {}) or {})
    supervisor = getattr(session, "_container_supervisor", None) or ContainerSupervisor()
    existing = supervisor.container_for_session(name)
    if existing is None:
        container_name = str(config.get("container_name") or f"mucli-{name}").strip()
        ref = supervisor.create(
            container_name=container_name,
            session_name=name,
            dockerfile=config.get("dockerfile"),
            template_name=config.get("template_name"),
            mounts=list(config.get("mounts") or []),
            egress_allow=list(config.get("egress_allow") or DEFAULT_EGRESS_ALLOW),
            egress_deny=list(config.get("egress_deny") or []),
            supervisor_url="",
            progress=_progress,
        )
        config.update(
            {
                "container_name": ref.name,
                "dockerfile": config.get("dockerfile"),
                "template_name": ref.template_name or config.get("template_name"),
                "mounts": [m.to_dict() for m in ref.mounts],
                "egress_allow": list(ref.egress_allow),
                "egress_deny": list(ref.egress_deny),
            }
        )
        manager.container_config = config
        manager.save_history(session.folder_context)
    else:
        _progress("starting_worker", f"Ensuring {existing.name} is running…")
        ref = supervisor.ensure_running(existing)
    session._container_supervisor = supervisor
    session.container_ref = ref
    return ref


def send_tui_container_message(session: Any, text: str) -> dict[str, Any]:
    supervisor = getattr(session, "_container_supervisor", None) or ContainerSupervisor()
    session._container_supervisor = supervisor
    ensure_tui_container(session)
    response = supervisor.send_sync(
        session.session_manager.current_session_name,
        text,
        provider=session.provider.name,
        model=session.provider.model_name,
        agent_mode=str(session.variables.get("agent_mode", "default")),
        system_instruction=session.system_instruction,
    )
    assistant_text = str(response.get("assistant_text") or "")
    if assistant_text and session.ui:
        session.ui.render_message("assistant", assistant_text, session.provider.model_name)
    # The worker wrote the mounted session JSON. Reload the host mirror so TUI
    # memory, traces, feature state, and token totals remain current.
    name = session.session_manager.current_session_name
    session.session_manager._load_session(name)
    session.sync_runtime_state()
    result = response.get("result")
    return result if isinstance(result, dict) else {"status": "complete"}


def interrupt_tui_container(session: Any) -> dict[str, Any]:
    supervisor = getattr(session, "_container_supervisor", None)
    if supervisor is None:
        return {"ok": False, "detail": "No container supervisor is active."}
    return supervisor.interrupt(session.session_manager.current_session_name)


def shutdown_tui_container(session: Any) -> None:
    supervisor = getattr(session, "_container_supervisor", None)
    if supervisor is not None:
        supervisor.shutdown()
