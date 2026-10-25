"""Helpers shared by the mode slash commands (/debug, /loop, /blocker, ...)."""

from __future__ import annotations

from typing import Any

from . import CommandResult


def emit(session: Any, body: str, allow_prompt: bool, *, error: bool = False) -> None:
    ui = getattr(session, "ui", None)
    if ui is None or not allow_prompt:
        return
    method = getattr(ui, "show_error" if error else "show_info", None) or getattr(ui, "show_info", None)
    if method is not None:
        try:
            method(body)
        except Exception:
            pass


def save(session: Any) -> None:
    manager = getattr(session, "session_manager", None)
    if manager is not None and getattr(manager, "current_session_name", "") and hasattr(manager, "save_history"):
        manager.save_history(getattr(session, "folder_context", None))


def reply(session: Any, body: str, allow_prompt: bool, *, ok: bool = True, data=None) -> CommandResult:
    emit(session, body, allow_prompt, error=not ok)
    return CommandResult(ok=ok, message=body, data=data or {})
