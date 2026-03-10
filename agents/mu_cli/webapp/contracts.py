from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ContractValidationError(ValueError):
    pass


def _expect_object(payload: Any, *, route: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContractValidationError(f"{route} payload must be a JSON object")
    return payload


def _opt_str(payload: dict[str, Any], key: str) -> str | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise ContractValidationError(f"{key} must be a string")
    return value


def _opt_bool(payload: dict[str, Any], key: str) -> bool | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, bool):
        raise ContractValidationError(f"{key} must be a boolean")
    return value


def _opt_int(payload: dict[str, Any], key: str) -> int | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, int):
        raise ContractValidationError(f"{key} must be an integer")
    return value


@dataclass(slots=True)
class ChatRequest:
    text: str
    session: str | None = None


@dataclass(slots=True)
class SessionActionRequest:
    action: str
    name: str
    payload: dict[str, Any]


@dataclass(slots=True)
class SettingsUpdateRequest:
    payload: dict[str, Any]


def parse_chat_request(raw: Any, *, route: str) -> ChatRequest:
    payload = _expect_object(raw, route=route)
    text = _opt_str(payload, "text")
    text = (text or "").strip()
    if not text:
        raise ContractValidationError("text is required")
    session = _opt_str(payload, "session")
    session = session.strip() if isinstance(session, str) else None
    return ChatRequest(text=text, session=session or None)


def parse_session_action_request(raw: Any) -> SessionActionRequest:
    payload = _expect_object(raw, route="/api/session")
    action = (_opt_str(payload, "action") or "").strip()
    if not action:
        raise ContractValidationError("action is required")
    name = (_opt_str(payload, "name") or "").strip()

    # typed checks for known mutable action fields
    for key in ("provider", "model", "openai_api_key", "google_api_key", "approval_mode", "workspace"):
        _opt_str(payload, key)
    for key in ("agentic_planning", "research_mode", "condense_enabled"):
        _opt_bool(payload, key)
    for key in ("max_runtime_seconds", "condense_window", "window"):
        _opt_int(payload, key)

    enabled_skills = payload.get("enabled_skills")
    if enabled_skills is not None:
        if not isinstance(enabled_skills, list) or any(not isinstance(item, str) for item in enabled_skills):
            raise ContractValidationError("enabled_skills must be a list of strings")

    return SessionActionRequest(action=action, name=name, payload=payload)


def parse_settings_update_request(raw: Any) -> SettingsUpdateRequest:
    payload = _expect_object(raw, route="/api/settings")
    for key in ("provider", "model", "openai_api_key", "google_api_key", "approval_mode", "workspace"):
        _opt_str(payload, key)
    for key in ("debug", "agentic_planning", "research_mode", "condense_enabled"):
        _opt_bool(payload, key)
    for key in ("max_runtime_seconds", "condense_window"):
        _opt_int(payload, key)

    tool_visibility = payload.get("tool_visibility")
    if tool_visibility is not None:
        if not isinstance(tool_visibility, dict):
            raise ContractValidationError("tool_visibility must be an object")
        for k, v in tool_visibility.items():
            if not isinstance(k, str) or not isinstance(v, bool):
                raise ContractValidationError("tool_visibility must be an object of booleans")

    custom_tools = payload.get("custom_tools")
    if custom_tools is not None and not isinstance(custom_tools, list):
        raise ContractValidationError("custom_tools must be a list")

    enabled_skills = payload.get("enabled_skills")
    if enabled_skills is not None:
        if not isinstance(enabled_skills, list) or any(not isinstance(item, str) for item in enabled_skills):
            raise ContractValidationError("enabled_skills must be a list of strings")

    return SettingsUpdateRequest(payload=payload)
