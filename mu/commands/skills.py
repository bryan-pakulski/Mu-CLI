"""Slash command for the skills registry."""

from typing import Any

from . import CommandResult, command


def _folders(session: Any) -> list[str]:
    fc = getattr(session, "folder_context", None)
    if fc is None:
        return []
    return list(getattr(fc, "folders", []) or [])


def _emit(session: Any, body: str, allow_prompt: bool) -> None:
    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "show_info") and allow_prompt:
        ui.show_info(body)


def _list_skills(session: Any, allow_prompt: bool) -> CommandResult:
    from mu.skills import discover_skills

    skills = discover_skills(_folders(session))
    disabled = set(getattr(session, "disabled_skills", []) or [])
    if not skills:
        msg = (
            "No skills installed. Drop a SKILL.md under "
            "`mu/skills/<name>/`, `~/.mu/skills/<name>/`, or "
            "`<workspace>/.mu/skills/<name>/`."
        )
        _emit(session, msg, allow_prompt)
        return CommandResult(ok=True, message=msg, data={"skills": []})

    lines = ["Installed skills:"]
    for skill in skills:
        tag = " [disabled]" if skill.name in disabled else ""
        trig = f"  [trigger: {skill.trigger}]" if skill.trigger else ""
        lines.append(f"  • [bold]{skill.name}[/bold]{tag} — {skill.description}{trig}")
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(
        ok=True,
        message=body,
        data={
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "trigger": s.trigger,
                    "disabled": s.name in disabled,
                }
                for s in skills
            ]
        },
    )


def _show_skill(session: Any, name: str, allow_prompt: bool) -> CommandResult:
    from mu.skills import discover_skills

    skills = discover_skills(_folders(session))
    for skill in skills:
        if skill.name.lower() == name.lower():
            body = (
                f"[bold]{skill.name}[/bold] — {skill.description}\n"
                f"[dim]source: {skill.source}[/dim]\n\n{skill.body}"
            )
            _emit(session, body, allow_prompt)
            return CommandResult(
                ok=True,
                message=skill.body,
                data={"name": skill.name, "source": skill.source},
            )
    return CommandResult(ok=False, message=f"No skill named {name!r}.")


def _reload(session: Any, allow_prompt: bool) -> CommandResult:
    from mu.skills import clear_skill_cache, discover_skills

    clear_skill_cache()
    skills = discover_skills(_folders(session))
    msg = f"Reloaded skills ({len(skills)} installed)."
    _emit(session, msg, allow_prompt)
    return CommandResult(ok=True, message=msg, data={"count": len(skills)})


def _toggle(session: Any, name: str, *, enable: bool, allow_prompt: bool) -> CommandResult:
    from mu.skills import get_skill

    if not name:
        verb = "enable" if enable else "disable"
        return CommandResult(ok=False, message=f"Usage: /skills {verb} <name>")
    skill = get_skill(name, _folders(session))
    if skill is None:
        return CommandResult(ok=False, message=f"No skill named {name!r}.")
    disabled = list(getattr(session, "disabled_skills", []) or [])
    if enable:
        if skill.name in disabled:
            disabled = [n for n in disabled if n != skill.name]
            session.disabled_skills = disabled
            msg = f"Enabled skill {skill.name!r}."
        else:
            msg = f"Skill {skill.name!r} is already enabled."
    else:
        if skill.name not in disabled:
            disabled.append(skill.name)
            session.disabled_skills = disabled
            msg = f"Disabled skill {skill.name!r}."
        else:
            msg = f"Skill {skill.name!r} is already disabled."
    _emit(session, msg, allow_prompt)
    return CommandResult(ok=True, message=msg, data={"disabled_skills": list(session.disabled_skills)})


@command(
    "/skills",
    help="List skills, show one (/skills <name>), or /skills {reload|enable|disable} <name>.",
)
def skills_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()
    if not raw:
        return _list_skills(session, allow_prompt)

    head, _, rest = raw.partition(" ")
    sub = head.lower()
    rest = rest.strip()

    if sub == "reload":
        return _reload(session, allow_prompt)
    if sub == "disable":
        return _toggle(session, rest, enable=False, allow_prompt=allow_prompt)
    if sub == "enable":
        return _toggle(session, rest, enable=True, allow_prompt=allow_prompt)

    # Fall through: treat the whole argument as a skill name to show.
    return _show_skill(session, raw, allow_prompt)
