"""System-prompt override editor API (``/api/system-prompts``).

Exposes the file-based prompt library (``mu.prompts``) to the GUI so a
prompt-editor panel can list, read, write, validate, and reload the base
and per-mode prompts that live under ``$MUCLI_HOME/prompts/``.

Distinct from ``routers/prompts.py`` (the prompt-response store that
unblocks the agent thread for ``ask_user_choice`` / approval prompts).

Resolution priority (highest first):
  1. runtime /set override (agentic_system_base_override / agentic_mode_prompt_<mode>)
  2. file under $MUCLI_HOME/prompts/
  3. hardcoded fallback in utils/config.py

PUT writes a file (layer 2); it does not touch the runtime /set override.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from mu.prompts import (
    acknowledge_stale,
    diff_override,
    get_resolved,
    init_templates,
    known_names,
    prompts_dir,
    read_override_raw,
    reload,
    reset_override,
    resolved_snapshot,
    stale_notice,
    stale_overrides,
    validate,
    write_override,
)

router = APIRouter()


class PromptPutBody(BaseModel):
    text: str
    version: Optional[int] = None


class PromptInitBody(BaseModel):
    names: Optional[List[str]] = None
    force: bool = False


def _session(request: Request) -> Any:
    try:
        return request.app.state.session_by_name()
    except Exception:
        return None


@router.get("")
async def list_prompts(request: Request):
    snap = resolved_snapshot(_session(request))
    stale = {item["name"]: item for item in stale_overrides()}
    return {
        "prompts_dir": prompts_dir(),
        "items": [
            {"name": name, **info, "stale": stale.get(name)}
            for name, info in snap.items()
        ],
        "notices": [
            {"name": item["name"], "text": stale_notice(item)}
            for item in stale.values()
            if not item["acknowledged"]
        ],
    }


@router.get("/{name}")
async def get_prompt(name: str, request: Request):
    if name not in known_names():
        raise HTTPException(status_code=404, detail=f"unknown prompt name: {name}")
    session = _session(request)
    var_key = (
        "agentic_system_base_override"
        if name == "base"
        else f"agentic_mode_prompt_{name}"
    )
    runtime = None
    if session is not None:
        val = session.variables.get(var_key)
        if isinstance(val, str) and val.strip():
            runtime = val
    resolved = get_resolved(name)
    text = runtime if runtime is not None else resolved.text
    return {
        "name": name,
        "text": text,
        "source": "override" if runtime is not None else resolved.source,
        "path": resolved.path,
        "version": resolved.version if runtime is None else None,
        "chars": len(text),
        "validation": validate(name, text),
        "raw_file": read_override_raw(name),
    }


@router.put("/{name}")
async def put_prompt(name: str, body: PromptPutBody):
    if name not in known_names():
        raise HTTPException(status_code=404, detail=f"unknown prompt name: {name}")
    path = write_override(name, body.text, version=body.version)
    return {
        "ok": True,
        "name": name,
        "path": path,
        "validation": validate(name, body.text),
    }


@router.post("/reload")
async def reload_prompts():
    reload()
    return {"ok": True}


@router.post("/init")
async def init_prompts(body: PromptInitBody):
    written = init_templates(body.names, force=body.force)
    return {"ok": True, "written": written}


@router.post("/{name}/reset")
async def reset_prompt(name: str):
    """Move the file override for ``name`` aside (``<name>.md.bak``) so the
    bundled prompt applies again."""
    if name not in known_names():
        raise HTTPException(status_code=404, detail=f"unknown prompt name: {name}")
    try:
        backup = reset_override(name)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "name": name, "removed": backup is not None, "backup": backup}


@router.post("/{name}/keep")
async def keep_prompt(name: str):
    """Keep an older override; its notice stays quiet until the bundled prompt changes."""
    if name not in known_names():
        raise HTTPException(status_code=404, detail=f"unknown prompt name: {name}")
    acknowledge_stale(name)
    return {"ok": True, "name": name}


@router.get("/{name}/diff")
async def prompt_diff(name: str):
    if name not in known_names():
        raise HTTPException(status_code=404, detail=f"unknown prompt name: {name}")
    return {"name": name, "diff": diff_override(name)}
