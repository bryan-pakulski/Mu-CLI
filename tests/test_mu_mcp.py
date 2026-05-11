"""Tests for the MCP stdio client and registry.

We use a tiny Python script as a fake MCP server. It speaks the line-
delimited JSON-RPC subset our client requires:

  * handshake → returns server capabilities + info
  * initialized notification → no response
  * tools/list → returns one tool
  * tools/call → echoes args back in a content block

This avoids depending on any external MCP server binary while still
exercising the full request/response/transport path.
"""

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

import mu.tools as mu_tools
from mu.mcp import MCPClient, MCPError, discover, register_all
from mu.mcp.registry import close_all


FAKE_SERVER_SCRIPT = textwrap.dedent(
    """
    import json, sys

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "fake", "version": "0.0.1"},
                    "capabilities": {"tools": {}},
                },
            }) + "\\n")
            sys.stdout.flush()
        elif method == "notifications/initialized":
            # notification → no response
            pass
        elif method == "tools/list":
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo the args.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                            },
                        }
                    ]
                },
            }) + "\\n")
            sys.stdout.flush()
        elif method == "tools/call":
            params = msg.get("params") or {}
            args = params.get("arguments") or {}
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": "echo:" + str(args.get("text", ""))}
                    ],
                    "isError": False,
                },
            }) + "\\n")
            sys.stdout.flush()
        else:
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": "Method not found"},
            }) + "\\n")
            sys.stdout.flush()
    """
).strip()


@pytest.fixture
def fake_server_script(tmp_path: Path) -> str:
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER_SCRIPT)
    return str(script)


def test_client_handshake_and_list_tools(fake_server_script):
    with MCPClient(command=[sys.executable, fake_server_script], name="fake") as client:
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "echo"
        assert tools[0].server == "fake"
        assert "text" in tools[0].input_schema.get("properties", {})


def test_client_call_tool_returns_content(fake_server_script):
    with MCPClient(command=[sys.executable, fake_server_script], name="fake") as client:
        result = client.call_tool("echo", {"text": "hello"})
        assert result["isError"] is False
        [block] = result["content"]
        assert block["text"] == "echo:hello"


def test_client_unknown_method_raises(fake_server_script):
    with MCPClient(command=[sys.executable, fake_server_script], name="fake") as client:
        with pytest.raises(MCPError):
            client._request("tools/nonsense", {})


def test_discover_missing_config_returns_empty(tmp_path):
    assert discover(str(tmp_path / "absent.json")) == {}


def test_discover_parses_config(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({
        "servers": {
            "fake": {"command": ["true"]}
        }
    }))
    servers = discover(str(cfg))
    assert "fake" in servers
    assert servers["fake"]["command"] == ["true"]


def test_register_all_registers_tools_into_mu_tools(tmp_path, fake_server_script):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({
        "servers": {
            "fake": {"command": [sys.executable, fake_server_script]},
        }
    }))
    clients = register_all(str(cfg))
    try:
        names = {t.name for t in mu_tools.list_tools()}
        assert "mcp__fake__echo" in names

        # Execute the registered MCP tool through the unified registry.
        ctx = mu_tools.build_tool_context(folder_context=None, ui=None, variables={})
        result = mu_tools.execute("mcp__fake__echo", {"text": "world"}, ctx)
        assert result["ok"] is True
        assert "echo:world" in result["message"]
        assert result["telemetry"]["mcp_server"] == "fake"
    finally:
        close_all(clients)


def test_register_all_skips_servers_that_fail_to_start(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({
        "servers": {
            "broken": {"command": ["/this/does/not/exist/binary"]},
        }
    }))
    clients = register_all(str(cfg))
    # Returned empty (or with the broken one closed); no exception.
    assert clients == []
