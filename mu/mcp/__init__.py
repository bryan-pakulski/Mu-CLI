"""MCP (Model Context Protocol) client and registry.

Modules:
  * client    — minimal stdio JSON-RPC client. Implements initialize,
                tools/list, tools/call. Lifecycle managed manually by
                callers (`open()`/`close()`).
  * registry  — discovers MCP servers from `.mu/mcp.json`, registers
                each server's tools into the `mu.tools` registry so the
                agent loop sees them uniformly with native tools.
"""

from .client import MCPClient, MCPError, MCPTool
from .registry import discover, register_all

__all__ = [
    "MCPClient",
    "MCPError",
    "MCPTool",
    "discover",
    "register_all",
]
