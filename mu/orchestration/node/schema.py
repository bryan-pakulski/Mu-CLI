"""A small JSON Schema subset, validated identically on the master and on every node.

Tool ``inputs`` and ``outputs`` are JSON Schema. The node must validate parameters locally
without third-party packages, so manifests are restricted to the keywords implemented here
(``unsupported_keywords`` lets the master reject anything else at draft time, which keeps the
two validators in agreement).
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List

SUPPORTED = frozenset({
    "type", "properties", "required", "additionalProperties", "enum", "const", "minimum",
    "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength", "pattern",
    "items", "minItems", "maxItems", "uniqueItems", "default", "description", "title",
    "examples", "format",
})
_TYPES = ("string", "integer", "number", "boolean", "array", "object", "null")
MAX_DEPTH = 8


def _is_type(value: Any, name: str) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    if name == "null":
        return value is None
    return False


def unsupported_keywords(schema: Any, path: str = "$", depth: int = 0) -> List[str]:
    """Problems that make a schema unusable on nodes (unknown keywords, bad types, depth)."""
    problems: List[str] = []
    if depth > MAX_DEPTH:
        return [f"{path}: schema nested deeper than {MAX_DEPTH}"]
    if not isinstance(schema, dict):
        return [f"{path}: schema must be an object"]
    for key in schema:
        if key not in SUPPORTED:
            problems.append(f"{path}: unsupported keyword {key!r}")
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind] if kind is not None else []
    for k in kinds:
        if k not in _TYPES:
            problems.append(f"{path}: unknown type {k!r}")
    if "pattern" in schema:
        try:
            re.compile(str(schema["pattern"]))
        except re.error as exc:
            problems.append(f"{path}: invalid pattern ({exc})")
    props = schema.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            problems.append(f"{path}.properties must be an object")
        else:
            for name, sub in props.items():
                problems += unsupported_keywords(sub, f"{path}.{name}", depth + 1)
    if isinstance(schema.get("additionalProperties"), dict):
        problems += unsupported_keywords(schema["additionalProperties"], f"{path}.*", depth + 1)
    if "items" in schema:
        problems += unsupported_keywords(schema["items"], f"{path}[]", depth + 1)
    return problems


def validate(value: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Return a list of human-readable errors (empty when valid)."""
    errors: List[str] = []
    if not isinstance(schema, dict):
        return [f"{path}: invalid schema"]
    kind = schema.get("type")
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_is_type(value, k) for k in kinds):
            return [f"{path}: expected {' or '.join(kinds)}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in list(schema["enum"] or []):
        errors.append(f"{path}: must be one of {list(schema['enum'])!r}")
    if _is_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: must be <= {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be > {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: must be < {schema['exclusiveMaximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema:
            try:
                if re.search(str(schema["pattern"]), value) is None:
                    errors.append(f"{path}: does not match {schema['pattern']!r}")
            except re.error:
                errors.append(f"{path}: invalid pattern")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            seen: List[Any] = []
            for item in value:
                if item in seen:
                    errors.append(f"{path}: items must be unique")
                    break
                seen.append(item)
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                errors += validate(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{path}.{name}: required")
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in props:
                errors += validate(item, props[name], f"{path}.{name}")
            elif extra is False:
                errors.append(f"{path}.{name}: not allowed")
            elif isinstance(extra, dict):
                errors += validate(item, extra, f"{path}.{name}")
    return errors


def apply_defaults(value: Any, schema: Dict[str, Any]) -> Any:
    """Fill top-level object property defaults (a copy; the input is not modified)."""
    if not isinstance(value, dict) or not isinstance(schema, dict):
        return value
    out = dict(value)
    for name, sub in (schema.get("properties") or {}).items():
        if name not in out and isinstance(sub, dict) and "default" in sub:
            out[name] = copy.deepcopy(sub["default"])
    return out
