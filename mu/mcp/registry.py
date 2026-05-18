"""Discover MCP servers and register their tools in the mu.tools registry.

Config (`.mu/mcp.json` in the working directory):

    {
      "servers": {
        "memory": {
          "command": ["mcp-server-memory"],
          "env": {"MEMORY_FILE": "/tmp/m.json"}
        },
        "fs": {
          "command": ["npx", "@modelcontextprotocol/server-filesystem", "/workspace"]
        }
      }
    }

Each tool surfaced by a server is registered as `mcp__<server>__<tool>`
in the mu.tools registry so collisions across servers are impossible.
The legacy `mu.tools.descriptors.TOOLS` list is also updated so the existing
system-prompt rendering picks them up.

Lifecycle: `register_all()` opens every configured client and registers
its tools, returning the open clients so callers can close them at
shutdown. Failures on a single server log a warning and continue —
one bad MCP server should not break the whole harness.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from mu import tools as mu_tools
from .client import MCPClient, MCPError, MCPTool


logger = logging.getLogger("mucli")


DEFAULT_CONFIG_PATH = os.path.join(".mu", "mcp.json")


def _safe_tool_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


def _build_handler(client: MCPClient, tool_name: str):
    def handler(args: Dict[str, Any], context) -> Dict[str, Any]:
        try:
            raw = client.call_tool(tool_name, args or {})
        except MCPError as exc:
            return {
                "ok": False,
                "error_code": "mcp_error",
                "message": str(exc),
                "data": {"tool": tool_name},
                "artifacts": [],
                "telemetry": {"tool_name": _safe_tool_name(client.name, tool_name)},
            }
        is_error = bool(raw.get("isError", False))
        content_list = raw.get("content") or []
        # MCP content blocks are typed; flatten the text-typed ones into
        # a single message string while preserving the full structured
        # content in `data`.
        text_chunks = []
        for block in content_list:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_chunks.append(str(block.get("text", "")))
        message = "\n".join(text_chunks) if text_chunks else ("error" if is_error else "ok")
        return {
            "ok": not is_error,
            "error_code": "mcp_tool_error" if is_error else None,
            "message": message,
            "data": {"content": content_list},
            "artifacts": [],
            "telemetry": {
                "tool_name": _safe_tool_name(client.name, tool_name),
                "mcp_server": client.name,
            },
        }
    handler.__name__ = f"mcp_{client.name}_{tool_name}"
    return handler


def _register_mcp_tool(client: MCPClient, tool: MCPTool) -> None:
    safe_name = _safe_tool_name(client.name, tool.name)
    # Use the existing @tool decorator so registrations land in the
    # same registry as native tools.
    decorator = mu_tools.tool(
        name=safe_name,
        description=f"[MCP:{client.name}] {tool.description}",
        parameters=tool.input_schema or {"type": "object", "properties": {}},
        requires_approval=True,
        execution_kind="io",
        result_mode="json",
        summary_builder=None,
    )
    decorator(_build_handler(client, tool.name))


def discover(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Dict[str, Any]]:
    """Return the parsed `servers` mapping from the config, or `{}` if no
    config exists. Caller decides whether to actually open them.
    """
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("mcp.json: failed to read %s: %s", config_path, exc)
        return {}
    servers = data.get("servers", {}) if isinstance(data, dict) else {}
    if not isinstance(servers, dict):
        return {}
    return servers


def register_all(
    config_path: str = DEFAULT_CONFIG_PATH,
    *,
    open_timeout: float = 10.0,
) -> List[MCPClient]:
    """Open every server in `.mu/mcp.json` and register their tools.

    Returns the list of opened clients so the caller can close them at
    process shutdown. Errors on individual servers are logged but do
    not propagate — partial registration is preferred over total failure.
    """
    servers = discover(config_path)
    if not servers:
        return []

    clients: List[MCPClient] = []
    for server_name, entry in servers.items():
        if not isinstance(entry, dict):
            logger.warning("mcp.json: server %r entry must be an object", server_name)
            continue
        command = entry.get("command")
        if not command:
            logger.warning("mcp.json: server %r missing 'command'", server_name)
            continue
        env = entry.get("env") if isinstance(entry.get("env"), dict) else None
        cwd = entry.get("cwd") if isinstance(entry.get("cwd"), str) else None

        client = MCPClient(
            command=command, name=server_name, env=env, cwd=cwd
        )
        try:
            client.open()
            mcp_tools = client.list_tools()
        except Exception as exc:
            logger.warning(
                "mcp.json: server %r failed to start or list tools: %s",
                server_name,
                exc,
            )
            client.close()
            continue

        for tool in mcp_tools:
            try:
                _register_mcp_tool(client, tool)
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning(
                    "mcp.json: server %r tool %r failed to register: %s",
                    server_name,
                    tool.name,
                    exc,
                )

        logger.info(
            "mcp.json: registered %d tool(s) from server %r",
            len(mcp_tools),
            server_name,
        )
        clients.append(client)

    return clients


def close_all(clients: List[MCPClient]) -> None:
    """Close every open MCP client. Safe to call with an empty list."""
    for client in clients:
        try:
            client.close()
        except Exception:  # pragma: no cover — defensive
            pass


__all__ = ["DEFAULT_CONFIG_PATH", "close_all", "discover", "register_all"]
