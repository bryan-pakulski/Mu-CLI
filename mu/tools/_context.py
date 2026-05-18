"""Re-export of `ToolExecutionContext` and `build_tool_context`.

These live in `core/tools.py` today. Imported through `mu.tools._context`
so the new agent loop can depend on `mu.tools.*` without reaching into
`core.*` directly.
"""

from mu.tools.descriptors import ToolExecutionContext, build_tool_context

__all__ = ["ToolExecutionContext", "build_tool_context"]
