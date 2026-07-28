"""Host-level managed container and template commands."""
from __future__ import annotations

import json
import os
import shlex
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from mu.container import ContainerSupervisor
from mu.container.docker_cli import ContainerRuntimeError
from mu.container.network import DEFAULT_EGRESS_ALLOW
from mu.container.tui import (
    _edit_dockerfile,
    _prompt_domain_list,
    _prompt_mounts,
)
from mu.container.builder import default_dockerfile
import utils.config as _config

from . import CommandResult, command

console = Console()


def _supervisor(session: Any) -> ContainerSupervisor:
    value = getattr(session, "_container_supervisor", None)
    if value is None:
        value = ContainerSupervisor()
        session._container_supervisor = value
    return value


def _show_containers(supervisor: ContainerSupervisor) -> list[dict]:
    refs = supervisor.list_environments()
    table = Table(title="Managed container environments")
    table.add_column("Name", style="cyan")
    table.add_column("State")
    table.add_column("Template")
    table.add_column("Sessions")
    table.add_column("Image", overflow="fold")
    rows = []
    for ref in refs:
        sessions = ", ".join(ref.attached_sessions) or "—"
        table.add_row(ref.name, ref.status, ref.template_name or "—", sessions, ref.image)
        rows.append(ref.to_dict(include_secret=False))
    if refs:
        console.print(table)
    else:
        console.print("[dim]No managed container environments.[/dim]")
    return rows


def _show_templates(supervisor: ContainerSupervisor) -> list[dict]:
    values = supervisor.template_registry.list_templates()
    table = Table(title="Container templates")
    table.add_column("Name", style="cyan")
    table.add_column("Source")
    table.add_column("Image", overflow="fold")
    table.add_column("Description")
    rows = []
    for item in values:
        table.add_row(item.name, item.source_container, item.image, item.description or "—")
        rows.append(item.to_dict())
    if values:
        console.print(table)
    else:
        console.print("[dim]No container templates.[/dim]")
    return rows


def _persist_binding(session: Any, session_name: str, config: dict[str, Any] | None) -> None:
    path = os.path.join(_config.HISTORY_DIR, "sessions", session_name, "session.json")
    if not os.path.isfile(path):
        raise ContainerRuntimeError(f"saved session not found: {session_name}")
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    variables = dict(data.get("variables") or {})
    if config is None:
        variables["session_type"] = "workspace"
        data.pop("container_config", None)
    else:
        variables.update({"session_type": "container", "yolo": True, "strict_mode": False})
        data["container_config"] = dict(config)
    data["variables"] = variables
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)

    if session.session_manager.current_session_name == session_name:
        session.variables.update(variables)
        session.session_manager.container_config = dict(config or {})
        session.container_ref = (
            _supervisor(session).resolve(config["container_name"]) if config else None
        )
        session.session_manager.save_history(session.folder_context)
        session.sync_runtime_state()


def _output(stream: str, text: str) -> None:
    if stream == "command":
        console.print(f"[dim]{text}[/dim]")
    elif stream == "stderr":
        console.print(f"[red]{text}[/red]")
    else:
        console.print(text, markup=False)


def _progress(stage: str, message: str) -> None:
    console.print(f"[cyan]container · {stage.replace('_', ' ')}[/cyan]  {message}")


def _create_environment(
    session: Any, raw: str, *, allow_prompt: bool
) -> CommandResult:
    parts = shlex.split(raw or "")
    name = None
    template_name = None
    start = True
    index = 0
    while index < len(parts):
        value = parts[index]
        if value in {"--template", "-t"} and index + 1 < len(parts):
            template_name = parts[index + 1]
            index += 2
            continue
        if value == "--no-start":
            start = False
            index += 1
            continue
        if name is None:
            name = value
        index += 1

    supervisor = _supervisor(session)
    if allow_prompt and not name:
        name = Prompt.ask("Container environment name").strip()
    if not name:
        return CommandResult(ok=False, message="Usage: /container create <name> [--template <name>]")

    dockerfile = None
    mounts: list[dict] = []
    allow = list(DEFAULT_EGRESS_ALLOW)
    deny: list[str] = []
    templates = supervisor.template_registry.list_templates()
    if allow_prompt and template_name is None and templates:
        source = Prompt.ask("Environment source", choices=["dockerfile", "template"], default="dockerfile")
        if source == "template":
            names = [item.name for item in templates]
            console.print("Templates: " + ", ".join(names), markup=False)
            template_name = Prompt.ask("Template", choices=names)

    if template_name is None:
        dockerfile = default_dockerfile()
        if allow_prompt and Confirm.ask("Edit the Dockerfile template", default=False):
            dockerfile = _edit_dockerfile(dockerfile)
        if allow_prompt:
            mounts = _prompt_mounts([])
            if Confirm.ask("Edit the egress allowlist", default=False):
                allow = _prompt_domain_list("Allowed domains (comma-separated)", allow)
            if Confirm.ask("Add blocked domains", default=False):
                deny = _prompt_domain_list("Blocked domains (comma-separated)", [])

    try:
        ref = supervisor.create_environment(
            container_name=name,
            dockerfile=dockerfile,
            template_name=template_name,
            mounts=mounts,
            egress_allow=allow if template_name is None else None,
            egress_deny=deny if template_name is None else None,
            start=start,
            progress=_progress if allow_prompt else None,
            output=_output if allow_prompt else None,
        )
    except (ContainerRuntimeError, RuntimeError, ValueError) as exc:
        return CommandResult(ok=False, message=str(exc))
    return CommandResult(
        ok=True,
        message=f"Created container environment: {ref.name}",
        data={"container": ref.to_dict(include_secret=False)},
    )


@command(
    "/container",
    help="Manage host container environments: list, create, start, stop, restart, shell, attach, detach, snapshot, remove.",
)
def container_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()
    head, _, rest = raw.partition(" ")
    sub = head.lower() if head else "list"
    rest = rest.strip()
    supervisor = _supervisor(session)

    try:
        if sub in {"list", "ls"}:
            rows = _show_containers(supervisor) if allow_prompt else [
                ref.to_dict(include_secret=False) for ref in supervisor.list_environments()
            ]
            return CommandResult(ok=True, message=f"{len(rows)} managed container(s).", data={"containers": rows})
        if sub in {"create", "new"}:
            return _create_environment(session, rest, allow_prompt=allow_prompt)

        parts = shlex.split(rest)
        name = parts[0] if parts else ""
        if not name:
            return CommandResult(ok=False, message=f"Usage: /container {sub} <container>")
        if sub == "start":
            ref = supervisor.start(name)
            return CommandResult(ok=True, message=f"Started {ref.name}")
        if sub == "stop":
            ref = supervisor.stop(name)
            return CommandResult(ok=True, message=f"Stopped {ref.name}")
        if sub == "restart":
            ref = supervisor.restart(name)
            return CommandResult(ok=True, message=f"Restarted {ref.name}")
        if sub == "shell":
            if not allow_prompt:
                return CommandResult(ok=False, message="Interactive shell requires a terminal.")
            shell = parts[1] if len(parts) > 1 else "/bin/bash"
            code = supervisor.interactive_shell(name, shell=shell)
            return CommandResult(ok=code == 0, message=f"Shell exited with status {code}")
        if sub == "attach":
            target_session = parts[1] if len(parts) > 1 else session.session_manager.current_session_name
            ref = supervisor.attach_session(name, target_session)
            _persist_binding(session, target_session, supervisor.configuration(ref.name))
            return CommandResult(ok=True, message=f"Attached {target_session} to {ref.name}")
        if sub == "detach":
            target_session = parts[1] if len(parts) > 1 else session.session_manager.current_session_name
            ref = supervisor.detach_session(name, target_session, stop_if_idle=False)
            _persist_binding(session, target_session, None)
            return CommandResult(ok=True, message=f"Detached {target_session} from {ref.name}")
        if sub == "snapshot":
            if len(parts) < 2:
                return CommandResult(ok=False, message="Usage: /container snapshot <container> <template> [description]")
            description = " ".join(parts[2:])
            item = supervisor.snapshot(name, parts[1], description=description)
            return CommandResult(ok=True, message=f"Created template: {item.name}", data={"template": item.to_dict()})
        if sub in {"remove", "rm", "delete"}:
            force = "--force" in parts[1:]
            removed = supervisor.remove(name, force=force)
            return CommandResult(ok=removed, message=(f"Removed {name}" if removed else f"Container not found: {name}"))
    except (ContainerRuntimeError, RuntimeError, ValueError) as exc:
        return CommandResult(ok=False, message=str(exc))

    return CommandResult(ok=False, message="Usage: /container [list|create|start|stop|restart|shell|attach|detach|snapshot|remove]")


@command(
    "/template",
    "/templates",
    help="Manage container templates: list, snapshot <container> <name>, use <template> <container>, delete <name>.",
)
def template_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()
    head, _, rest = raw.partition(" ")
    sub = head.lower() if head else "list"
    parts = shlex.split(rest)
    supervisor = _supervisor(session)
    try:
        if sub in {"list", "ls"}:
            rows = _show_templates(supervisor) if allow_prompt else [
                item.to_dict() for item in supervisor.template_registry.list_templates()
            ]
            return CommandResult(ok=True, message=f"{len(rows)} template(s).", data={"templates": rows})
        if sub == "snapshot" and len(parts) >= 2:
            item = supervisor.snapshot(parts[0], parts[1], description=" ".join(parts[2:]))
            return CommandResult(ok=True, message=f"Created template: {item.name}", data={"template": item.to_dict()})
        if sub in {"use", "create"} and len(parts) >= 2:
            return _create_environment(
                session,
                f"{shlex.quote(parts[1])} --template {shlex.quote(parts[0])}",
                allow_prompt=allow_prompt,
            )
        if sub in {"delete", "remove", "rm"} and parts:
            removed = supervisor.remove_template(parts[0])
            return CommandResult(ok=removed, message=(f"Removed template {parts[0]}" if removed else f"Template not found: {parts[0]}"))
    except (ContainerRuntimeError, RuntimeError, ValueError) as exc:
        return CommandResult(ok=False, message=str(exc))
    return CommandResult(ok=False, message="Usage: /template [list|snapshot <container> <name>|use <template> <container>|delete <name>]")
