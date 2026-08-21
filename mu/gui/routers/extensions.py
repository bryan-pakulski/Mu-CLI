"""Extensions router — generic extension registry API.

Extensions register themselves with tool definitions, system prompt blocks,
and a version. The backend stores them on session.extensions and dynamically
augments the system prompt + tool interception.

Endpoints:
  POST /api/extensions/register          — Register an extension
  POST /api/extensions/{ext_id}/tool_result — Extension posts tool result
  GET  /api/extensions                   — List active extensions
  POST /api/extensions/{ext_id}/unregister — Remove an extension
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


# ---------------------------------------------------------------- models

class ToolParam(BaseModel):
    name: str
    type: str
    description: str = ""
    required: bool = False


class ToolDefinition(BaseModel):
    name: str
    description: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ExtensionRegister(BaseModel):
    extension_id: str
    version: str = "1.0.0"
    tools: List[ToolDefinition] = Field(default_factory=list)
    system_prompt: str = ""
    tool_prefix: str = ""  # e.g. "nvim_" — tools matching this prefix are dispatched to extension


class ToolResult(BaseModel):
    call_id: str
    result: Optional[Any] = None
    error: str = ""


# ---------------------------------------------------------------- helpers

def _get_session_manager():
    """Lazy import to avoid circular deps."""
    from mu.session.manager import SessionManager
    return SessionManager


def _resolve_session(session_name: str = ""):
    """Get session by name or fall back to active session."""
    from mu.gui.routers.chat import _get_session as _chat_get_session
    return _chat_get_session(session_name) if session_name else _chat_get_session()


# ---------------------------------------------------------------- routes

@router.post("/register")
async def register_extension(body: ExtensionRegister, session_name: str = ""):
    """Register an extension on the session.

    Stores tool definitions, system prompt, and version on session.extensions.
    The system prompt builder will append extension system prompts.
    Tool interception dispatches tools matching tool_prefix to the extension
    via extension_tool_call SSE events.
    """
    session = _resolve_session(session_name)
    if session is None:
        raise HTTPException(status_code=404, detail="No active session")

    if not hasattr(session, "extensions"):
        session.extensions = {}

    ext_data = {
        "version": body.version,
        "tools": [t.model_dump() for t in body.tools],
        "system_prompt": body.system_prompt,
        "tool_prefix": body.tool_prefix,
    }
    session.extensions[body.extension_id] = ext_data

    return {
        "ok": True,
        "extension_id": body.extension_id,
        "version": body.version,
        "tools_registered": len(body.tools),
    }


@router.post("/{extension_id}/tool_result")
async def extension_tool_result(extension_id: str, body: ToolResult, session_name: str = ""):
    """Receive tool execution result from an extension.

    Called by the extension plugin after it executes a tool dispatched via
    extension_tool_call SSE event. Resolves the pending tool call so the
    agent loop can continue.
    """
    from mu.session.tools_glue import resolve_extension_tool_result
    ok = resolve_extension_tool_result(body.call_id, result=body.result, error=body.error)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Unknown call_id: {body.call_id}")
    return {"ok": True}


@router.get("")
async def list_extensions(session_name: str = ""):
    """List active extensions on the session."""
    session = _resolve_session(session_name)
    if session is None:
        raise HTTPException(status_code=404, detail="No active session")

    extensions = getattr(session, "extensions", {})
    return {
        "extensions": [
            {
                "extension_id": ext_id,
                "version": data.get("version", "unknown"),
                "tool_prefix": data.get("tool_prefix", ""),
                "tools_count": len(data.get("tools", [])),
            }
            for ext_id, data in extensions.items()
        ]
    }


@router.post("/{extension_id}/unregister")
async def unregister_extension(extension_id: str, session_name: str = ""):
    """Remove an extension from the session."""
    session = _resolve_session(session_name)
    if session is None:
        raise HTTPException(status_code=404, detail="No active session")

    extensions = getattr(session, "extensions", {})
    if extension_id not in extensions:
        raise HTTPException(status_code=404, detail=f"Extension not registered: {extension_id}")

    del session.extensions[extension_id]
    return {"ok": True, "extension_id": extension_id}