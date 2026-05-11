"""Tool registry for the new agent loop.

The registry exposes three primary operations:

    @tool(name=..., description=..., parameters=..., ...)        # register a handler
    def my_handler(args, context): ...

    execute(name, args, context) -> envelope dict                # invoke a tool
    list_tools(*, disabled=set()) -> list[ToolDefinition]        # enumerate tools
    get(name) -> ToolDescriptor | None                           # introspect

The registry is populated from two sources:

  1. **Legacy bridge.** At import time we pull every ToolDescriptor and
     handler from `core/tools.py` into this registry. That preserves the
     61-tool surface while the per-domain migration is in progress.

  2. **@tool-decorated handlers.** New tools register here directly. They
     coexist with legacy tools and use the same envelope contract.

The new `@tool` decorator produces a `ToolDescriptor` with the same shape as
the legacy code, so callers don't need to know which path a tool came from.
"""

from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from providers.base import ToolDefinition

from ._context import ToolExecutionContext, build_tool_context  # re-exports


# ToolDescriptor lives in legacy `core.tools` for now; we re-export it so
# that consumers of `mu.tools` don't reach into `core` directly.
def _import_legacy():
    from core import tools as _legacy  # noqa: WPS433 — intentional late import
    return _legacy


_REGISTRY: Dict[str, "ToolDescriptor"] = {}
_HANDLERS: Dict[str, Callable[..., Any]] = {}
_LEGACY_LOADED = False


def _ensure_legacy_loaded() -> None:
    """Lazily import legacy registry on first use.

    Done lazily to avoid circular import problems: `core/tools.py` does
    its own heavy work at import time and may itself want to register
    callbacks against the new registry in the future.
    """
    global _LEGACY_LOADED
    if _LEGACY_LOADED:
        return
    legacy = _import_legacy()
    for name, descriptor in getattr(legacy, "TOOL_DESCRIPTORS", {}).items():
        _REGISTRY.setdefault(name, descriptor)
    for name, handler in getattr(legacy, "TOOL_HANDLERS", {}).items():
        _HANDLERS.setdefault(name, handler)
    _LEGACY_LOADED = True


# ---------------------------------------------------------------- registration


def tool(
    *,
    name: str,
    description: str,
    parameters: Dict[str, Any],
    requires_approval: bool = True,
    execution_kind: str = "io",
    preview_policy: str = "default",
    server_policy: str = "default",
    result_mode: str = "json",
    error_mode: str = "text_error",
    summary_builder: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that registers a handler as a tool in the new registry.

    The handler signature is `(args: dict, context: ToolExecutionContext)`
    and may return either a string, a dict with an envelope shape, or a
    dict carrying a `success`/`error` pair. `execute()` wraps the return
    value through the same envelope builder the legacy harness uses.
    """

    legacy = _import_legacy()
    build_descriptor = getattr(legacy, "_build_descriptor")

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        definition = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            requires_approval=requires_approval,
        )
        descriptor = build_descriptor(
            definition,
            execution_kind=execution_kind,
            preview_policy=preview_policy,
            server_policy=server_policy,
            result_mode=result_mode,
            handler_key=name,
            error_mode=error_mode,
            summary_builder=summary_builder,
        )
        _REGISTRY[name] = descriptor
        _HANDLERS[name] = func
        # Mirror into the legacy maps too so old code finds the new tool.
        legacy.TOOL_DESCRIPTORS[name] = descriptor
        legacy.TOOL_HANDLERS[name] = func
        # Append to TOOLS so list-style consumers (UI, system prompt) see it.
        existing = {t.name for t in legacy.TOOLS}
        if name not in existing:
            legacy.TOOLS.append(definition)
        return func

    return decorator


# ---------------------------------------------------------------- introspection


def get(name: str) -> Optional["ToolDescriptor"]:
    _ensure_legacy_loaded()
    return _REGISTRY.get(name)


def list_tools(*, disabled: Optional[Iterable[str]] = None) -> List[ToolDefinition]:
    _ensure_legacy_loaded()
    skip: Set[str] = set(disabled or ())
    return [
        descriptor.definition
        for name, descriptor in _REGISTRY.items()
        if name not in skip
    ]


def list_descriptors() -> List["ToolDescriptor"]:
    _ensure_legacy_loaded()
    return list(_REGISTRY.values())


# ----------------------------------------------------------------- execution


def execute(name: str, args: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
    """Run a tool and return a normalized envelope dict.

    Delegates to the legacy `execute_tool` so the envelope contract,
    approval-mode gating, telemetry, and edge-case handling all stay in
    one place. Once the legacy module is removed, this function will own
    the dispatch directly.

    Legacy `execute_tool` returns a JSON string (so it can be inlined into
    message history). We parse it back into a dict here because the new
    agent loop wants structured access.
    """

    import json as _json

    legacy = _import_legacy()
    _ensure_legacy_loaded()
    raw = legacy.execute_tool(
        tool_name=name,
        args=args,
        folder_context=context.folder_context,
        ui=context.ui,
        variables=context.variables,
        invocation_source=context.invocation_source,
        session=context.session,
    )
    if isinstance(raw, dict):
        return raw
    try:
        parsed = _json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        pass
    # Fall back to wrapping the raw text in an envelope so callers always
    # get a structured result.
    return {
        "ok": True,
        "error_code": None,
        "message": str(raw or ""),
        "data": {},
        "artifacts": [],
        "telemetry": {"tool_name": name},
    }


def execute_raw(name: str, args: Dict[str, Any], context: ToolExecutionContext) -> str:
    """Run a tool and return the raw JSON-string envelope (legacy contract)."""
    legacy = _import_legacy()
    _ensure_legacy_loaded()
    return legacy.execute_tool(
        tool_name=name,
        args=args,
        folder_context=context.folder_context,
        ui=context.ui,
        variables=context.variables,
        invocation_source=context.invocation_source,
        session=context.session,
    )


def _load_builtin_tools() -> None:
    """Import built-in `@tool`-decorated modules so their handlers register.

    Done at the bottom of `__init__.py` so the `tool` decorator and
    `_REGISTRY` are defined first. The import is wrapped in try/except so
    a single bad tool module doesn't kill the whole registry.
    """
    try:
        from . import task  # noqa: F401 — registers todo_write/set_status/list
    except Exception as exc:  # pragma: no cover — defensive
        import logging
        logging.getLogger("mucli").warning(
            "mu.tools: failed to load task tool package: %s", exc
        )
    try:
        from . import agent as _agent_tools  # noqa: F401 — registers spawn_agent
    except Exception as exc:  # pragma: no cover — defensive
        import logging
        logging.getLogger("mucli").warning(
            "mu.tools: failed to load agent tool package: %s", exc
        )


_load_builtin_tools()


__all__ = [
    "ToolExecutionContext",
    "build_tool_context",
    "execute",
    "execute_raw",
    "get",
    "list_descriptors",
    "list_tools",
    "tool",
]
