"""Approval previews for tools that run commands they don't take as arguments.

``verify_security_proof(finding_id)`` runs a command stored on the finding;
approving the call must show that command (and the PoC script) rather than an
opaque id. A tool registers a preview builder; the approval prompt merges its
fields into the displayed arguments.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

_PREVIEWS: Dict[str, Callable[[Dict[str, Any], Any], Dict[str, Any]]] = {}
SCRIPT_PREVIEW_CHARS = 4000


def register_preview(tool_name: str):
    def decorator(fn):
        _PREVIEWS[tool_name] = fn
        return fn

    return decorator


def approval_preview(tool_name: str, args: Dict[str, Any], session: Any) -> Dict[str, Any]:
    """Extra fields to show beside a call's arguments ({} when none)."""
    fn = _PREVIEWS.get(str(tool_name or ""))
    if fn is None:
        return {}
    try:
        return dict(fn(dict(args or {}), session) or {})
    except Exception as exc:  # noqa: BLE001 — a broken preview must not hide the call
        logger.debug("approval preview for %s failed", tool_name, exc_info=True)
        return {"preview_error": str(exc)}


def has_preview(tool_name: str) -> bool:
    return str(tool_name or "") in _PREVIEWS


__all__ = ["SCRIPT_PREVIEW_CHARS", "approval_preview", "has_preview", "register_preview"]
