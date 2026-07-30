"""Visualization extraction helpers for durable conversation history.

MUCLI_VISUALIZATION_TIMELINE_V2
"""
from __future__ import annotations

import json
from typing import Any, Iterable


_ALLOWED_FIELDS = {
    "artifact_id",
    "name",
    "title",
    "size",
    "mime_type",
    "created_at",
    "kind",
    "display",
    "height",
    "view_url",
    "download_url",
}


def _decode_json_layers(value: Any, limit: int = 4) -> Any:
    current = value
    for _ in range(limit):
        if not isinstance(current, str):
            break
        stripped = current.strip()
        if not stripped or stripped[:1] not in {"{", "[", '"'}:
            break
        try:
            current = json.loads(stripped)
        except (TypeError, ValueError):
            break
    return current


def _normalized_visualization(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("kind") != "visualization":
        return None
    artifact_id = str(value.get("artifact_id") or "").strip()
    if not artifact_id:
        return None
    out = {key: value[key] for key in _ALLOWED_FIELDS if key in value}
    out["artifact_id"] = artifact_id
    out["kind"] = "visualization"
    return out


def extract_visualization(value: Any) -> dict[str, Any] | None:
    """Recursively recover one visualization descriptor from tool output.

    Tool transports may wrap results in JSON strings, ``data``, ``result``,
    ``content`` or list envelopes. The old extractor only handled three fixed
    dictionary paths, which caused valid visualizations to fall back to the end
    of conversation history.
    """
    root = _decode_json_layers(value)
    stack: list[tuple[Any, int]] = [(root, 0)]
    visited = 0

    while stack and visited < 1000:
        current, depth = stack.pop()
        visited += 1
        current = _decode_json_layers(current)

        direct = _normalized_visualization(current)
        if direct is not None:
            return direct
        if depth >= 8:
            continue

        if isinstance(current, dict):
            # Visit common result wrappers first while still supporting arbitrary
            # nested tool response shapes.
            priority = (
                "artifact",
                "artifacts",
                "data",
                "result",
                "output",
                "content",
                "value",
                "payload",
            )
            seen_keys = set()
            for key in reversed(priority):
                if key in current:
                    stack.append((current[key], depth + 1))
                    seen_keys.add(key)
            for key, child in reversed(list(current.items())):
                if key not in seen_keys:
                    stack.append((child, depth + 1))
        elif isinstance(current, (list, tuple)):
            for child in reversed(current):
                stack.append((child, depth + 1))

    return None


def match_visualization_reference(
    value: Any,
    candidates: Iterable[dict[str, Any]],
    seen_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """Match a registry descriptor when raw tool output references its ID."""
    seen = seen_ids or set()
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)

    for candidate in candidates:
        artifact_id = str(candidate.get("artifact_id") or "")
        if artifact_id and artifact_id not in seen and artifact_id in text:
            return dict(candidate)
    return None


def merge_registry_descriptor(
    extracted: dict[str, Any] | None,
    registry_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if extracted is None:
        return None
    artifact_id = str(extracted.get("artifact_id") or "")
    registered = registry_by_id.get(artifact_id)
    if not registered:
        return extracted
    merged = dict(extracted)
    merged.update(registered)
    return merged
