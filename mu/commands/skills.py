"""Slash command for the skills registry."""

from typing import Any

from . import CommandResult, command


@command("/skills", help="List installed skills, or show one: /skills <name>.")
def skills_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    from mu.skills import discover_skills

    folders = []
    fc = getattr(session, "folder_context", None)
    if fc is not None:
        folders = list(getattr(fc, "folders", []) or [])
    skills = discover_skills(folders)

    name = (args or "").strip()
    if name:
        for skill in skills:
            if skill.name.lower() == name.lower():
                body = (
                    f"[bold]{skill.name}[/bold] — {skill.description}\n"
                    f"[dim]source: {skill.source}[/dim]\n\n{skill.body}"
                )
                ui = getattr(session, "ui", None)
                if ui is not None and hasattr(ui, "show_info") and allow_prompt:
                    ui.show_info(body)
                return CommandResult(
                    ok=True,
                    message=skill.body,
                    data={"name": skill.name, "source": skill.source},
                )
        return CommandResult(ok=False, message=f"No skill named {name!r}.")

    if not skills:
        msg = (
            "No skills installed. Drop a SKILL.md under "
            "`mu/skills/<name>/`, `~/.mu/skills/<name>/`, or "
            "`<workspace>/.mu/skills/<name>/`."
        )
        ui = getattr(session, "ui", None)
        if ui is not None and hasattr(ui, "show_info") and allow_prompt:
            ui.show_info(msg)
        return CommandResult(ok=True, message=msg, data={"skills": []})

    lines = ["Installed skills:"]
    for skill in skills:
        lines.append(f"  • [bold]{skill.name}[/bold] — {skill.description}")
    body = "\n".join(lines)
    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "show_info") and allow_prompt:
        ui.show_info(body)
    return CommandResult(
        ok=True,
        message=body,
        data={"skills": [{"name": s.name, "description": s.description} for s in skills]},
    )
