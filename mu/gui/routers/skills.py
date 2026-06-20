"""Skills management — list, enable, disable, reload, save, delete, read."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..deps import require_session

router = APIRouter()


def _folders(session) -> List[str]:
    fc = getattr(session, "folder_context", None)
    if fc is None:
        return []
    return list(getattr(fc, "folders", []) or [])


def _skills_list(session) -> List[Dict[str, Any]]:
    from mu.skills import discover_skills

    disabled = set(getattr(session, "disabled_skills", []) or [])
    skills = discover_skills(_folders(session))
    return [
        {
            "name": s.name,
            "description": s.description,
            "trigger": s.trigger,
            "source": s.source,
            "body": s.body,
            "enabled": s.name not in disabled,
        }
        for s in skills
    ]


_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


def _resolve_scope_dir(scope: str, session) -> Path:
    if scope == "global":
        return Path(os.path.expanduser("~/.mu/skills"))
    folders = _folders(session)
    if not folders:
        raise HTTPException(
            status_code=400,
            detail="No workspace attached — cannot save workspace-scoped skill.",
        )
    return Path(folders[0]) / ".mu" / "skills"


@router.get("")
async def list_skills(session=Depends(require_session)) -> Dict[str, Any]:
    return {"skills": _skills_list(session)}


class SkillSaveBody(BaseModel):
    name: str
    description: str = ""
    trigger: str = ""
    body: str = ""
    scope: str = "global"


@router.post("/save")
async def save_skill(
    payload: SkillSaveBody, session=Depends(require_session)
) -> Dict[str, Any]:
    from mu.skills import clear_skill_cache

    slug = payload.name.strip()
    if not slug or not _SAFE_NAME.match(slug):
        raise HTTPException(
            status_code=400,
            detail="Skill name must be alphanumeric, hyphens, or underscores only.",
        )

    scope_dir = _resolve_scope_dir(payload.scope, session)
    skill_dir = scope_dir / slug
    skill_dir.mkdir(parents=True, exist_ok=True)

    lines = ["---"]
    lines.append(f"name: {slug}")
    if payload.description:
        lines.append(f"description: {payload.description}")
    if payload.trigger:
        lines.append(f"trigger: {payload.trigger}")
    lines.append("---")
    if payload.body:
        lines.append("")
        lines.append(payload.body)

    (skill_dir / "SKILL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    clear_skill_cache()
    return {"ok": True, "skills": _skills_list(session)}


@router.post("/reload")
async def reload_skills(session=Depends(require_session)) -> Dict[str, Any]:
    from mu.skills import clear_skill_cache

    clear_skill_cache()
    return {"ok": True, "skills": _skills_list(session)}


@router.get("/{name}")
async def read_skill(
    name: str, session=Depends(require_session)
) -> Dict[str, Any]:
    from mu.skills import get_skill

    skill = get_skill(name, _folders(session))
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found.")
    return {
        "name": skill.name,
        "description": skill.description,
        "trigger": skill.trigger,
        "body": skill.body,
        "source": skill.source,
    }


@router.post("/{name}/enable")
async def enable_skill(
    name: str, request: Request, session=Depends(require_session)
) -> Dict[str, Any]:
    from mu.skills import get_skill

    skill = get_skill(name, _folders(session))
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found.")
    disabled = list(getattr(session, "disabled_skills", []) or [])
    session.disabled_skills = [n for n in disabled if n != skill.name]
    return {"ok": True, "skills": _skills_list(session)}


@router.post("/{name}/disable")
async def disable_skill(
    name: str, request: Request, session=Depends(require_session)
) -> Dict[str, Any]:
    from mu.skills import get_skill

    skill = get_skill(name, _folders(session))
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found.")
    disabled = list(getattr(session, "disabled_skills", []) or [])
    if skill.name not in disabled:
        disabled.append(skill.name)
    session.disabled_skills = disabled
    return {"ok": True, "skills": _skills_list(session)}


@router.delete("/{name}")
async def delete_skill(
    name: str, session=Depends(require_session)
) -> Dict[str, Any]:
    from mu.skills import clear_skill_cache, get_skill

    skill = get_skill(name, _folders(session))
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found.")

    builtin_dir = os.path.dirname(os.path.abspath(__import__("mu.skills", fromlist=["__file__"]).__file__))
    if skill.source.startswith(builtin_dir):
        raise HTTPException(status_code=403, detail="Cannot delete built-in skills.")

    source = Path(skill.source)
    if source.is_file():
        source.unlink()
    parent = source.parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()

    clear_skill_cache()
    return {"ok": True, "skills": _skills_list(session)}
