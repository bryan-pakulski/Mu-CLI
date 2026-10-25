"""Helpers shared by mode tool handlers: envelopes and error mapping."""

from __future__ import annotations

import functools
from typing import Any, Callable, Dict, Optional

from .events import GateError
from .evidence import CheckRefused


def envelope(tool_name: str, ok: bool, message: str, data: Optional[Dict[str, Any]] = None,
             error_code: Optional[str] = None) -> Dict[str, Any]:
    return {
        "ok": bool(ok),
        "error_code": None if ok else (error_code or "invalid_args"),
        "message": message,
        "data": data or {},
        "artifacts": [],
        "telemetry": {"tool_name": tool_name},
    }


def guarded(tool_name: str) -> Callable:
    """Map engine refusals to failure envelopes the model can act on."""

    def decorator(fn: Callable[[Dict[str, Any], Any], Dict[str, Any]]):
        @functools.wraps(fn)
        def wrapper(args: Dict[str, Any], context: Any) -> Dict[str, Any]:
            try:
                return fn(args or {}, context)
            except CheckRefused as exc:
                return envelope(tool_name, False, str(exc), error_code=exc.code)
            except GateError as exc:
                return envelope(tool_name, False, str(exc), dict(exc.data or {}), exc.code)
            except (ValueError, KeyError) as exc:
                text = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
                return envelope(tool_name, False, str(text), error_code="invalid_args")

        return wrapper

    return decorator


def owner(context: Any) -> Any:
    """The object mode engines resolve state from (the tool context when it has a session)."""
    return context if getattr(context, "session", None) is not None else None


def mode_of(context: Any) -> str:
    variables = getattr(context, "variables", None) or {}
    session = getattr(context, "session", None)
    if not variables and session is not None:
        variables = getattr(session, "variables", None) or {}
    return str(variables.get("agent_mode", "default") or "default").lower()


def run_line(run: Any) -> str:
    mark = "✓" if run.passed else "✕"
    return f"{mark} {run.id} {run.label or run.purpose}: {run.detail}"


__all__ = ["envelope", "guarded", "mode_of", "owner", "run_line"]
