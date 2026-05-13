"""Pin the /mcp slash command (list / status / reload / debug)."""

import json
import sys
import textwrap
from pathlib import Path

import pytest

import mu.commands as mc
import mu.tools as mu_tools
from core.session import Session, SessionManager
from mu.mcp import close_all, register_all
from providers.base import LLMProvider, ProviderResponse


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
            pass
        elif method == "tools/list":
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": [
                    {"name": "echo", "description": "Echo.",
                     "inputSchema": {"type": "object",
                                     "properties": {"text": {"type": "string"}}}}
                ]},
            }) + "\\n")
            sys.stdout.flush()
        elif method == "tools/call":
            params = msg.get("params") or {}
            args = params.get("arguments") or {}
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [
                    {"type": "text", "text": "echo:" + str(args.get("text", ""))}
                ], "isError": False},
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


class _DummyProvider(LLMProvider):
    def get_available_models(self):
        return ["dummy"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="", parts=[])

    def upload_file(self, file_path, mime_type):
        return None


@pytest.fixture
def session():
    sm = SessionManager()
    sess = Session(_DummyProvider("dummy"), False, "system instruction", sm)
    sess._mcp_clients = []
    return sess


@pytest.fixture
def fake_server_script(tmp_path: Path) -> str:
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER_SCRIPT)
    return str(script)


@pytest.fixture
def mcp_session(session, fake_server_script, tmp_path, monkeypatch):
    """Session with one real, running MCP server (the fake echo server)."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / ".mu"
    cfg_dir.mkdir()
    cfg = cfg_dir / "mcp.json"
    cfg.write_text(json.dumps({
        "servers": {"fake": {"command": [sys.executable, fake_server_script]}}
    }))
    clients = register_all(str(cfg))
    session._mcp_clients = clients
    yield session
    close_all(clients)
    # Drop the registered MCP tool so other tests start clean.
    for entry in list(mu_tools.list_tools()):
        if entry.name.startswith("mcp__fake__"):
            mu_tools.unregister(entry.name)


def test_mcp_no_args_lists_servers(mcp_session):
    result = mc.dispatch(mcp_session, "/mcp", allow_prompt=False)
    assert result.ok
    names = {s["name"] for s in result.data["servers"]}
    assert "fake" in names


def test_mcp_list_shows_status(mcp_session):
    result = mc.dispatch(mcp_session, "/mcp list", allow_prompt=False)
    assert result.ok
    row = next(s for s in result.data["servers"] if s["name"] == "fake")
    assert row["status"] == "ok"


def test_mcp_status_includes_tool_count_and_capabilities(mcp_session):
    result = mc.dispatch(mcp_session, "/mcp status", allow_prompt=False)
    assert result.ok
    row = next(s for s in result.data["servers"] if s["name"] == "fake")
    assert row["tools"] >= 1
    assert "tools" in row["capabilities"]
    assert row["server_info"].get("name") == "fake"


def test_mcp_debug_requires_name(mcp_session):
    result = mc.dispatch(mcp_session, "/mcp debug", allow_prompt=False)
    assert result.ok is False
    assert "Usage" in result.message


def test_mcp_debug_unknown_server_errors(mcp_session):
    result = mc.dispatch(mcp_session, "/mcp debug does-not-exist", allow_prompt=False)
    assert result.ok is False


def test_mcp_debug_returns_server_info_for_running_server(mcp_session):
    result = mc.dispatch(mcp_session, "/mcp debug fake", allow_prompt=False)
    assert result.ok
    assert result.data["server"] == "fake"
    assert result.data["status"] == "ok"
    assert result.data["server_info"].get("name") == "fake"


def test_mcp_reload_restarts_servers(mcp_session):
    old_clients = list(mcp_session._mcp_clients)
    result = mc.dispatch(mcp_session, "/mcp reload", allow_prompt=False)
    assert result.ok
    # Old clients should be closed; new clients should be alive.
    for old in old_clients:
        assert old._proc is None
    new_names = {c.name for c in mcp_session._mcp_clients}
    assert "fake" in new_names


def test_mcp_rejects_unknown_subcommand(session):
    result = mc.dispatch(session, "/mcp nope", allow_prompt=False)
    assert result is not None
    assert result.ok is False


def test_mcp_handles_empty_config_gracefully(session, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = mc.dispatch(session, "/mcp list", allow_prompt=False)
    assert result.ok
    assert result.data["servers"] == []
