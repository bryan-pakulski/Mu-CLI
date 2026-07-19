"""``/prompts`` — inspect, reload, init, validate, and edit file-based
system-prompt overrides.

Subcommands:

    /prompts                 — list every prompt and its current source
    /prompts reload          — clear the prompt cache (pick up file edits)
    /prompts init [name|all] — write template files to $MUCLI_HOME/prompts/
    /prompts show <name>     — print the currently-effective prompt text
    /prompts validate [name] — check critical tool-surface anchors are present
    /prompts edit <name>     — open the override file in $EDITOR (or print path)

Resolution priority (highest first):
  1. runtime /set override (agentic_system_base_override / agentic_mode_prompt_<mode>)
  2. file under $MUCLI_HOME/prompts/
  3. hardcoded fallback in utils/config.py
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from . import CommandResult, command


def _emit(session: Any, body: str, allow_prompt: bool, *, error: bool = False) -> None:
    ui = getattr(session, "ui", None)
    if ui is None or not allow_prompt:
        return
    fn = getattr(ui, "show_error", None) if error else getattr(ui, "show_info", None)
    if fn is None:
        fn = getattr(ui, "show_info", None)
    if fn is not None:
        try:
            fn(body)
        except Exception:
            pass


def _list(session: Any, allow_prompt: bool) -> CommandResult:
    from mu.prompts import known_names, prompts_dir, resolved_snapshot

    snap = resolved_snapshot(session)
    lines = ["System-prompt resolution:", ""]
    lines.append(f"  override dir: {prompts_dir()}")
    lines.append("")
    lines.append(f"  {'name':<10} {'source':<12} {'version':<8} {'chars':>7}  detail")
    for name in known_names():
        row = snap.get(name, {})
        source = row.get("source", "?")
        version = row.get("version")
        chars = int(row.get("chars", 0) or 0)
        vstr = str(version) if version is not None else "—"
        detail = ""
        if row.get("has_override"):
            detail = "(/set override active)"
        elif source == "file":
            detail = f"file: {row.get('path')}"
        elif source == "hardcoded":
            detail = "hardcoded fallback"
        lines.append(
            f"  {name:<10} {source:<12} {vstr:<8} {chars:>7,}  {detail}"
        )
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message=body, data={"snapshot": snap})


def _reload(session: Any, allow_prompt: bool) -> CommandResult:
    from mu.prompts import reload as _reload_lib

    _reload_lib()
    # Also clear the skills discovery cache so /skills reload isn't needed
    # separately when prompts and skills are edited together.
    try:
        from mu.skills import clear_skill_cache

        clear_skill_cache()
    except Exception:
        pass
    msg = "Prompt cache cleared. File edits will apply on the next turn."
    _emit(session, f"[bold green]{msg}[/bold green]", allow_prompt)
    return CommandResult(ok=True, message=msg)


def _init(session: Any, args: str, allow_prompt: bool) -> CommandResult:
    from mu.prompts import init_templates, known_names, prompts_dir

    target = (args or "").strip().lower()
    if target in ("", "all"):
        names = None  # all
        label = "all prompts"
    else:
        if target not in known_names():
            return CommandResult(
                ok=False,
                message=(
                    f"Unknown prompt name {target!r}. Valid: "
                    f"{', '.join(known_names())}"
                ),
            )
        names = [target]
        label = target
    written = init_templates(names)
    if not written:
        msg = (
            f"No files written (all existing). Use `/prompts init {label}` after "
            f"deleting the file(s) under {prompts_dir()}, or edit them in place."
        )
        _emit(session, msg, allow_prompt)
        return CommandResult(ok=True, message=msg, data={"written": {}})
    body = "Wrote template(s):\n" + "\n".join(
        f"  {n} -> {p}" for n, p in written.items()
    )
    body += "\nEdit the file(s); changes apply after `/prompts reload`."
    _emit(session, f"[bold green]{body}[/bold green]", allow_prompt)
    return CommandResult(ok=True, message=body, data={"written": written})


def _show(session: Any, name: str, allow_prompt: bool) -> CommandResult:
    from mu.prompts import get_resolved, known_names

    if name not in known_names():
        return CommandResult(
            ok=False,
            message=f"Unknown prompt name {name!r}. Valid: {', '.join(known_names())}",
        )
    resolved = get_resolved(name)
    # Honor a runtime /set override if present.
    var_key = (
        "agentic_system_base_override"
        if name == "base"
        else f"agentic_mode_prompt_{name}"
    )
    runtime = session.variables.get(var_key)
    text = runtime if (isinstance(runtime, str) and runtime.strip()) else resolved.text
    src = "override" if runtime else resolved.source
    header = f"### {name} ({src}, {len(text):,} chars)"
    body = f"{header}\n\n{text}"
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message=body, data={"name": name, "source": src, "chars": len(text)})


def _validate(session: Any, args: str, allow_prompt: bool) -> CommandResult:
    from mu.prompts import get_resolved, known_names, validate

    target = (args or "").strip().lower()
    names = [target] if target and target in known_names() else known_names()
    if target and target not in known_names():
        return CommandResult(
            ok=False,
            message=f"Unknown prompt name {target!r}. Valid: {', '.join(known_names())}",
        )
    lines = ["Prompt validation:"]
    any_missing = False
    for name in names:
        var_key = (
            "agentic_system_base_override"
            if name == "base"
            else f"agentic_mode_prompt_{name}"
        )
        runtime = session.variables.get(var_key)
        text = runtime if (isinstance(runtime, str) and runtime.strip()) else get_resolved(name).text
        missing = validate(name, text)
        if not missing:
            lines.append(f"  [green]{name}[/green]: OK")
        else:
            any_missing = True
            lines.append(
                f"  [red]{name}[/red]: missing critical anchors — "
                + ", ".join(missing)
            )
    body = "\n".join(lines)
    _emit(session, body, allow_prompt, error=any_missing)
    return CommandResult(ok=not any_missing, message=body)


def _edit(session: Any, name: str, allow_prompt: bool) -> CommandResult:
    from mu.prompts import _file_path, init_templates, known_names, prompts_dir

    if name not in known_names():
        return CommandResult(
            ok=False,
            message=f"Unknown prompt name {name!r}. Valid: {', '.join(known_names())}",
        )
    path = _file_path(name)
    if not os.path.isfile(path):
        init_templates([name])
        _emit(
            session,
            f"Seeded {path} from the current prompt. Edit and run /prompts reload.",
            allow_prompt,
        )
    editor = os.environ.get("EDITOR")
    if editor:
        try:
            subprocess.call([editor, path])
            return CommandResult(ok=True, message=f"Opened {path} with {editor}")
        except Exception as exc:
            return CommandResult(ok=False, message=f"Failed to launch $EDITOR: {exc}")
    _emit(
        session,
        f"No $EDITOR set. Edit this file then run /prompts reload:\n  {path}",
        allow_prompt,
    )
    return CommandResult(ok=True, message=f"Edit {path}, then /prompts reload. (dir: {prompts_dir()})")


@command(
    "/prompts",
    help=(
        "Inspect / manage file-based system-prompt overrides. "
        "Subcommands: reload, init [name|all], show <name>, validate [name], edit <name>."
    ),
)
def prompts_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    parts = (args or "").split(None, 1)
    sub = (parts[0] or "").lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "":
        return _list(session, allow_prompt)
    if sub == "reload":
        return _reload(session, allow_prompt)
    if sub == "init":
        return _init(session, rest, allow_prompt)
    if sub == "show":
        if not rest:
            return CommandResult(ok=False, message="Usage: /prompts show <name>")
        return _show(session, rest.lower(), allow_prompt)
    if sub == "validate":
        return _validate(session, rest, allow_prompt)
    if sub == "edit":
        if not rest:
            return CommandResult(ok=False, message="Usage: /prompts edit <name>")
        return _edit(session, rest.lower(), allow_prompt)
    return CommandResult(
        ok=False,
        message=(
            "Unknown /prompts subcommand. Usage: /prompts [reload|init|show|validate|edit]"
        ),
    )