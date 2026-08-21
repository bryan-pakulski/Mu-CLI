"""Runtime extension registry used by editor clients.

Extensions are session-scoped and ephemeral. They advertise provider-visible
tools, receive matching calls over the GUI event stream, and resolve those
calls through the result endpoint below. Registration is bound to a client id
so a stale editor cannot answer a newer editor's call.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter()

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]+$")
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_MAX_TOOLS = 32
_MAX_SYSTEM_PROMPT = 32_000
_MAX_SCHEMA_CHARS = 128_000
_MAX_EDITOR_REQUEST_CHARS = 64_000
_EXTENSION_TTL_SECONDS = 90.0


class ToolDefinition(BaseModel):
    name: str
    description: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)
    execution_kind: str = "read"


class ExtensionRegister(BaseModel):
    extension_id: str
    client_id: str
    session_name: str = ""
    version: str = "1.0.0"
    tools: List[ToolDefinition] = Field(default_factory=list)
    system_prompt: str = ""
    tool_prefix: str = ""
    capabilities: List[str] = Field(default_factory=list)


class ExtensionHeartbeat(BaseModel):
    client_id: str
    session_name: str


class ToolResult(BaseModel):
    call_id: str
    client_id: str
    session_name: str
    result: Optional[Any] = None
    error: str = ""


class ExtensionRequest(BaseModel):
    client_id: str
    session_name: str
    kind: str = "editor"
    prompt: str


def _resolve_session(request: Request, session_name: str = ""):
    return request.app.state.session_by_name(session_name or None)


def _validate_registration(body: ExtensionRegister) -> None:
    if not (1 <= len(body.extension_id) <= 64) or not _IDENTIFIER.fullmatch(
        body.extension_id
    ):
        raise HTTPException(status_code=400, detail="Invalid extension_id")
    if not (1 <= len(body.client_id) <= 128) or not _IDENTIFIER.fullmatch(
        body.client_id
    ):
        raise HTTPException(status_code=400, detail="Invalid client_id")
    if body.tool_prefix and not _TOOL_NAME.fullmatch(body.tool_prefix + "x"):
        raise HTTPException(status_code=400, detail="Invalid tool_prefix")
    if len(body.tools) > _MAX_TOOLS:
        raise HTTPException(
            status_code=400, detail=f"At most {_MAX_TOOLS} extension tools are allowed"
        )
    if len(body.system_prompt) > _MAX_SYSTEM_PROMPT:
        raise HTTPException(
            status_code=400, detail="Extension system prompt is too large"
        )

    seen: set[str] = set()
    schema_chars = 0
    for tool in body.tools:
        if not _TOOL_NAME.fullmatch(tool.name):
            raise HTTPException(
                status_code=400, detail=f"Invalid tool name: {tool.name}"
            )
        if body.tool_prefix and not tool.name.startswith(body.tool_prefix):
            raise HTTPException(
                status_code=400,
                detail=f"Tool {tool.name!r} does not match prefix {body.tool_prefix!r}",
            )
        if tool.name in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate tool: {tool.name}")
        seen.add(tool.name)
        if not isinstance(tool.parameters, dict):
            raise HTTPException(
                status_code=400, detail=f"Invalid schema for {tool.name}"
            )
        if tool.execution_kind not in {"read", "mutate"}:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid execution_kind for {tool.name}",
            )
        if len(tool.description) > 4_000:
            raise HTTPException(
                status_code=400, detail=f"Description is too large for {tool.name}"
            )
        schema_chars += len(str(tool.parameters))
    if schema_chars > _MAX_SCHEMA_CHARS:
        raise HTTPException(
            status_code=400, detail="Extension tool schemas are too large"
        )

    # Built-in tools must always execute through their registered descriptors.
    try:
        from mu.tools.descriptors import TOOLS

        collision = {tool.name for tool in TOOLS}.intersection(seen)
        if collision:
            raise HTTPException(
                status_code=409,
                detail=f"Extension tools collide with built-ins: {sorted(collision)}",
            )
    except HTTPException:
        raise
    except Exception:
        pass


@router.post("/register")
async def register_extension(body: ExtensionRegister, request: Request):
    """Register or replace one extension client on a loaded session."""

    _validate_registration(body)
    session = _resolve_session(request, body.session_name)
    if session is None:
        raise HTTPException(status_code=404, detail="No active session")

    if not hasattr(session, "extensions"):
        session.extensions = {}

    previous = session.extensions.get(body.extension_id)
    if previous and previous.get("client_id") != body.client_id:
        from mu.session.tools_glue import cancel_extension_calls

        cancel_extension_calls(
            body.extension_id,
            session.session_manager.current_session_name,
            client_id=str(previous.get("client_id") or ""),
            reason="Editor client was replaced by a newer registration",
        )

    session.extensions[body.extension_id] = {
        "version": body.version,
        "client_id": body.client_id,
        "tools": [
            tool.model_dump() if hasattr(tool, "model_dump") else tool.dict()
            for tool in body.tools
        ],
        "system_prompt": body.system_prompt,
        "tool_prefix": body.tool_prefix,
        "capabilities": list(dict.fromkeys(body.capabilities)),
        "registered_at": time.time(),
        "last_seen": time.time(),
    }

    return {
        "ok": True,
        "extension_id": body.extension_id,
        "client_id": body.client_id,
        "version": body.version,
        "tools_registered": len(body.tools),
        "session_name": session.session_manager.current_session_name,
    }


@router.post("/{extension_id}/heartbeat")
async def extension_heartbeat(
    extension_id: str, body: ExtensionHeartbeat, request: Request
):
    if not body.client_id or not body.session_name:
        raise HTTPException(
            status_code=400, detail="client_id and session_name are required"
        )
    session = _resolve_session(request, body.session_name)
    extensions = getattr(session, "extensions", {}) if session is not None else {}
    data = extensions.get(extension_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Extension is not registered")
    if data.get("client_id") != body.client_id:
        raise HTTPException(status_code=409, detail="Extension client was replaced")
    data["last_seen"] = time.time()
    return {"ok": True}


@router.post("/{extension_id}/tool_result")
async def extension_tool_result(extension_id: str, body: ToolResult, request: Request):
    """Resolve only a call owned by this extension, session, and client."""

    if not body.call_id or not body.client_id or not body.session_name:
        raise HTTPException(
            status_code=400,
            detail="call_id, client_id, and session_name are required",
        )

    session = _resolve_session(request, body.session_name)
    data = (getattr(session, "extensions", {}) if session is not None else {}).get(
        extension_id
    )
    if data is None:
        raise HTTPException(status_code=404, detail="Extension is not registered")
    if data.get("client_id") != body.client_id:
        raise HTTPException(status_code=409, detail="Extension client was replaced")
    data["last_seen"] = time.time()

    from mu.session.tools_glue import resolve_extension_tool_result

    ok = resolve_extension_tool_result(
        body.call_id,
        result=body.result,
        error=body.error,
        extension_id=extension_id,
        client_id=body.client_id,
        session_name=body.session_name,
    )
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"Unknown or stale call_id: {body.call_id}"
        )
    return {"ok": True}


@router.post("/{extension_id}/request")
async def extension_request(
    extension_id: str, body: ExtensionRequest, request: Request
):
    """Run a history-free editor intelligence request.

    Hints and inline completions should not inject machine-oriented prompts or
    structured payloads into the user's durable chat history. This endpoint
    uses the session's provider under its normal session lock, records usage,
    but deliberately supplies no tools and stores no conversation turn.
    """

    if not body.client_id or not body.session_name:
        raise HTTPException(
            status_code=400, detail="client_id and session_name are required"
        )
    if not (1 <= len(body.kind) <= 32) or not _IDENTIFIER.fullmatch(body.kind):
        raise HTTPException(status_code=400, detail="Invalid editor request kind")
    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    if len(body.prompt) > _MAX_EDITOR_REQUEST_CHARS:
        raise HTTPException(status_code=413, detail="Editor request is too large")

    session = _resolve_session(request, body.session_name)
    data = (getattr(session, "extensions", {}) if session is not None else {}).get(
        extension_id
    )
    if data is None:
        raise HTTPException(status_code=404, detail="Extension is not registered")
    if data.get("client_id") != body.client_id:
        raise HTTPException(status_code=409, detail="Extension client was replaced")
    data["last_seen"] = time.time()
    if getattr(session, "provider", None) is None:
        raise HTTPException(status_code=409, detail="Session has no provider")
    if request.app.state.session_busy_for(body.session_name).is_set():
        raise HTTPException(
            status_code=409, detail="Session already has a turn in progress"
        )

    from providers.base import Message, MessagePart

    system_prompt = (
        "You are MUCLI's Neovim intelligence service. Follow the requested "
        "output contract exactly. Treat live editor content as authoritative. "
        "Do not claim to have changed files."
    )
    extension_prompt = str(data.get("system_prompt") or "").strip()
    if extension_prompt:
        system_prompt += "\n\n" + extension_prompt
    system_prompt += (
        "\n\nFor this history-free editor request, no tools are available. "
        "Answer only from the supplied prompt and obey its output shape exactly."
    )

    def generate():
        lock = request.app.state.session_lock_for(body.session_name)
        with lock:
            response = session.provider.generate(
                messages=[
                    Message(
                        role="user",
                        parts=[MessagePart(type="text", text=body.prompt)],
                    )
                ],
                system_prompt=system_prompt,
                thinking=False,
                tools=None,
            )
            manager = session.session_manager
            counts = manager.token_counts
            counts["input"] = counts.get("input", 0) + int(response.input_tokens or 0)
            counts["output"] = counts.get("output", 0) + int(
                response.output_tokens or 0
            )
            total = int(response.total_tokens or 0) or int(
                (response.input_tokens or 0) + (response.output_tokens or 0)
            )
            counts["total"] = counts.get("total", 0) + total
            counts["cached"] = counts.get("cached", 0) + int(
                response.cached_tokens or 0
            )
            counts["reasoning"] = counts.get("reasoning", 0) + int(
                response.reasoning_tokens or 0
            )
            try:
                from utils.config import calculate_cost

                cost = calculate_cost(
                    session.provider.model_name,
                    int(response.input_tokens or 0),
                    int(response.output_tokens or 0),
                )
                if cost is not None:
                    counts["total_cost"] = counts.get("total_cost", 0.0) + cost
            except Exception:
                pass
            manager.save_history(session.folder_context)
            return response

    try:
        response = await asyncio.to_thread(generate)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Editor request failed: {exc}"
        ) from exc

    return {
        "ok": True,
        "kind": body.kind,
        "text": str(response.text or ""),
        "usage": {
            "input": int(response.input_tokens or 0),
            "output": int(response.output_tokens or 0),
            "total": int(response.total_tokens or 0)
            or int((response.input_tokens or 0) + (response.output_tokens or 0)),
        },
    }


@router.get("")
async def list_extensions(request: Request, session_name: str = ""):
    session = _resolve_session(request, session_name)
    if session is None:
        raise HTTPException(status_code=404, detail="No active session")

    extensions = getattr(session, "extensions", {})
    return {
        "extensions": [
            {
                "extension_id": extension_id,
                "client_id": data.get("client_id", ""),
                "version": data.get("version", "unknown"),
                "tool_prefix": data.get("tool_prefix", ""),
                "tools_count": len(data.get("tools", [])),
                "capabilities": list(data.get("capabilities", [])),
                "last_seen": data.get("last_seen"),
                "connected": (
                    not data.get("last_seen")
                    or time.time() - float(data["last_seen"]) <= _EXTENSION_TTL_SECONDS
                ),
            }
            for extension_id, data in sorted(extensions.items())
        ]
    }


@router.post("/{extension_id}/unregister")
async def unregister_extension(
    extension_id: str,
    request: Request,
    session_name: str = Query(default=""),
    client_id: str = Query(default=""),
):
    session = _resolve_session(request, session_name)
    if session is None:
        raise HTTPException(status_code=404, detail="No active session")

    extensions = getattr(session, "extensions", {})
    data = extensions.get(extension_id)
    if data is None:
        return {"ok": True, "extension_id": extension_id, "already_absent": True}
    if client_id and data.get("client_id") != client_id:
        raise HTTPException(status_code=409, detail="Extension client was replaced")

    from mu.session.tools_glue import cancel_extension_calls

    cancel_extension_calls(
        extension_id,
        session.session_manager.current_session_name,
        client_id=client_id or None,
        reason="Editor extension disconnected",
    )
    del extensions[extension_id]
    return {"ok": True, "extension_id": extension_id}
