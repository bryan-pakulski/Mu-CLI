"""Minimal MCP (Model Context Protocol) stdio client.

Implements the subset of MCP required to expose remote tools to the
agent loop:

  * `initialize`           — handshake; returns server capabilities.
  * `notifications/initialized` — required follow-up notification.
  * `tools/list`           — discover available tools.
  * `tools/call`           — invoke a tool by name with JSON args.

Transport: a child process speaking JSON-RPC 2.0 over stdin/stdout, one
message per line. This is the "stdio" transport defined in the MCP spec.
HTTP/SSE transport is a future addition; the request/response and id
plumbing in this client is transport-agnostic so HTTP can slot in by
swapping out `_send` and `_recv`.

This is intentionally a single-threaded blocking client suitable for the
synchronous agent loop. Concurrent requests would need a request-id
demultiplexer; not needed today.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


logger = logging.getLogger("mucli")


class MCPError(RuntimeError):
    """Raised on protocol errors, transport failures, or tool errors."""


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    # The server this tool came from — used by the registry to namespace
    # tools in the global mu.tools registry.
    server: str = ""


class MCPClient:
    """A minimal JSON-RPC-over-stdio client for a single MCP server.

    Usage:

        client = MCPClient(command=["my-mcp-server"], name="myserver")
        client.open()
        try:
            tools = client.list_tools()
            result = client.call_tool("greet", {"name": "world"})
        finally:
            client.close()
    """

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(
        self,
        *,
        command: Iterable[str],
        name: str = "",
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        read_timeout: float = 30.0,
    ):
        self.command: List[str] = list(command)
        if not self.command:
            raise ValueError("MCPClient: command must not be empty")
        self.name = name or self.command[0]
        self.env = env
        self.cwd = cwd
        self.read_timeout = read_timeout

        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._capabilities: Dict[str, Any] = {}
        self._server_info: Dict[str, Any] = {}

    # ---------------------------------------------------------- lifecycle

    def open(self) -> None:
        if self._proc is not None:
            return
        proc_env = os.environ.copy()
        if self.env:
            proc_env.update(self.env)
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=proc_env,
            cwd=self.cwd,
            text=True,
            bufsize=1,  # line-buffered
        )
        try:
            response = self._request(
                "initialize",
                {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "mucli", "version": "0.1"},
                },
            )
        except Exception:
            self.close()
            raise
        self._server_info = response.get("serverInfo", {}) if isinstance(response, dict) else {}
        self._capabilities = response.get("capabilities", {}) if isinstance(response, dict) else {}
        # The protocol requires a 'notifications/initialized' notification
        # after the handshake completes.
        self._notify("notifications/initialized", {})

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2.0)
        except Exception:  # pragma: no cover — defensive
            pass
        finally:
            self._proc = None

    def __enter__(self) -> "MCPClient":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ----------------------------------------------------------- tools API

    def list_tools(self) -> List[MCPTool]:
        response = self._request("tools/list", {})
        tools: List[MCPTool] = []
        for entry in (response or {}).get("tools", []):
            if not isinstance(entry, dict):
                continue
            tools.append(
                MCPTool(
                    name=str(entry.get("name", "")),
                    description=str(entry.get("description", "")),
                    input_schema=entry.get("inputSchema") or {},
                    server=self.name,
                )
            )
        return tools

    def call_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        response = self._request(
            "tools/call",
            {"name": name, "arguments": args or {}},
        )
        if not isinstance(response, dict):
            return {"content": [], "isError": False}
        return response

    # ------------------------------------------------------------- internals

    def _ensure_open(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            raise MCPError(
                f"MCP server '{self.name}' is not running. Call open() first."
            )

    def _send(self, message: Dict[str, Any]) -> None:
        self._ensure_open()
        assert self._proc is not None and self._proc.stdin is not None
        line = json.dumps(message) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPError(f"MCP server '{self.name}' transport failed: {exc}")

    def _recv(self) -> Dict[str, Any]:
        self._ensure_open()
        assert self._proc is not None and self._proc.stdout is not None
        # Block reading a line. The MCP stdio transport guarantees one
        # message per line; lines that fail JSON decode are skipped (the
        # server may also emit unsolicited notifications which we ignore).
        while True:
            line = self._proc.stdout.readline()
            if not line:
                stderr = ""
                if self._proc and self._proc.stderr:
                    try:
                        stderr = self._proc.stderr.read() or ""
                    except Exception:
                        stderr = ""
                raise MCPError(
                    f"MCP server '{self.name}' closed unexpectedly. stderr={stderr[:500]}"
                )
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                logger.warning(
                    "MCP '%s' emitted non-JSON line: %s", self.name, line[:200]
                )
                continue

    def _request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": method,
                    "params": params,
                }
            )
            # Drain messages until we see the matching response.
            while True:
                message = self._recv()
                if not isinstance(message, dict):
                    continue
                if message.get("id") != req_id:
                    # Notification or out-of-order; for our single-flight
                    # client we just log and continue.
                    if "method" in message:
                        logger.debug(
                            "MCP '%s' notification: %s", self.name, message.get("method")
                        )
                    continue
                if "error" in message:
                    err = message["error"] or {}
                    raise MCPError(
                        f"MCP '{self.name}' error {err.get('code')}: {err.get('message')}"
                    )
                return message.get("result") or {}

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        with self._lock:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                }
            )

    # --------------------------------------------------------------- repr

    def __repr__(self) -> str:
        return f"MCPClient(name={self.name!r}, command={self.command!r})"


__all__ = ["MCPClient", "MCPError", "MCPTool"]
