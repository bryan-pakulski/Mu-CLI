"""Slash command for managing MCP servers at runtime.

    /mcp                   — list configured servers + status (alias of /mcp list)
    /mcp list              — same as above
    /mcp status            — tool counts + capabilities per server
    /mcp reload            — tear down + re-register from .mu/mcp.json
    /mcp debug <server>    — show last-error / stderr captured for one server
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import CommandResult, command


def _clients(session: Any) -> List[Any]:
    return list(getattr(session, "_mcp_clients", []) or [])


def _emit(session: Any, body: str, allow_prompt: bool) -> None:
    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "show_info") and allow_prompt:
        ui.show_info(body)


def _emit_error(session: Any, body: str, allow_prompt: bool) -> None:
    ui = getattr(session, "ui", None)
    if ui is not None and hasattr(ui, "show_error") and allow_prompt:
        ui.show_error(body)


def _client_status(client: Any) -> str:
    proc = getattr(client, "_proc", None)
    if proc is None:
        return "not started"
    poll = proc.poll() if hasattr(proc, "poll") else None
    if poll is None:
        return "ok"
    return f"dead (exit {poll})"


def _capabilities(client: Any) -> Dict[str, Any]:
    return getattr(client, "_capabilities", {}) or {}


def _server_info(client: Any) -> Dict[str, Any]:
    return getattr(client, "_server_info", {}) or {}


def _configured_names(session: Any) -> List[str]:
    """Names from .mu/mcp.json — including any that failed to start."""
    try:
        from mu.mcp import discover

        return sorted(discover().keys())
    except Exception:
        return []


def _running_by_name(session: Any) -> Dict[str, Any]:
    return {c.name: c for c in _clients(session)}


def _list(session: Any, allow_prompt: bool) -> CommandResult:
    running = _running_by_name(session)
    configured = _configured_names(session)
    all_names = sorted(set(configured) | set(running))

    rows = []
    for name in all_names:
        client = running.get(name)
        if client is None:
            status = "configured (not started — restart with /mcp reload)"
        else:
            status = _client_status(client)
        rows.append({"name": name, "status": status})

    if not rows:
        msg = (
            "No MCP servers configured. Drop a `.mu/mcp.json` with a "
            "`servers` object. See documentation/mcp.md."
        )
        _emit(session, msg, allow_prompt)
        return CommandResult(ok=True, message=msg, data={"servers": []})

    lines = ["MCP servers:"]
    for row in rows:
        lines.append(f"  • [bold]{row['name']}[/bold] — {row['status']}")
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message=body, data={"servers": rows})


def _status(session: Any, allow_prompt: bool) -> CommandResult:
    rows = []
    for client in _clients(session):
        try:
            from mu.tools import list_tools

            tool_count = sum(
                1 for t in list_tools() if t.name.startswith(f"mcp__{client.name}__")
            )
        except Exception:
            tool_count = 0
        rows.append(
            {
                "name": client.name,
                "status": _client_status(client),
                "tools": tool_count,
                "capabilities": _capabilities(client),
                "server_info": _server_info(client),
            }
        )

    if not rows:
        msg = "No MCP servers are running."
        _emit(session, msg, allow_prompt)
        return CommandResult(ok=True, message=msg, data={"servers": []})

    lines = ["MCP server status:"]
    for row in rows:
        info = row["server_info"]
        caps = ", ".join(sorted(row["capabilities"].keys())) or "(none)"
        title = info.get("name") or row["name"]
        version = info.get("version") or ""
        version_str = f" v{version}" if version else ""
        lines.append(
            f"  • [bold]{row['name']}[/bold] — {row['status']}, "
            f"{row['tools']} tool(s) [dim]({title}{version_str}, caps: {caps})[/dim]"
        )
    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(ok=True, message=body, data={"servers": rows})


def _reload(session: Any, allow_prompt: bool) -> CommandResult:
    try:
        from mu.mcp import close_all, register_all
    except ImportError as exc:
        msg = f"MCP module not available: {exc}"
        _emit_error(session, msg, allow_prompt)
        return CommandResult(ok=False, message=msg)

    existing = _clients(session)
    close_all(existing)
    try:
        from mu import tools as mu_tools

        # Drop existing MCP tools so the re-handshake can re-register
        # against any new schemas the server now advertises.
        for entry in list(mu_tools.list_tools()):
            if entry.name.startswith("mcp__"):
                mu_tools.unregister(entry.name)
    except Exception:
        # If unregistration fails we still proceed — register_all below
        # at worst leaves stale tool entries alongside the new ones.
        pass

    try:
        new_clients = register_all()
    except Exception as exc:
        msg = f"MCP reload failed: {exc}"
        _emit_error(session, msg, allow_prompt)
        session._mcp_clients = []
        return CommandResult(ok=False, message=msg)

    session._mcp_clients = new_clients
    msg = f"Reloaded MCP: {len(new_clients)} server(s) running."
    _emit(session, msg, allow_prompt)
    return CommandResult(
        ok=True,
        message=msg,
        data={"servers": [{"name": c.name, "status": _client_status(c)} for c in new_clients]},
    )


def _debug(session: Any, name: str, allow_prompt: bool) -> CommandResult:
    if not name:
        msg = "Usage: /mcp debug <server>"
        _emit_error(session, msg, allow_prompt)
        return CommandResult(ok=False, message=msg)

    running = _running_by_name(session)
    client = running.get(name)
    if client is None:
        msg = (
            f"No running MCP server named {name!r}. "
            "Run /mcp list to see configured servers."
        )
        _emit_error(session, msg, allow_prompt)
        return CommandResult(ok=False, message=msg, data={"server": name})

    stderr_text = ""
    proc = getattr(client, "_proc", None)
    if proc is not None and proc.stderr is not None and proc.poll() is not None:
        try:
            stderr_text = proc.stderr.read() or ""
        except Exception:
            stderr_text = ""

    lines = [f"MCP debug: {name}"]
    lines.append(f"  status: {_client_status(client)}")
    lines.append(f"  command: {getattr(client, 'command', [])}")
    info = _server_info(client)
    if info:
        lines.append(
            f"  server_info: {info.get('name', '')} v{info.get('version', '')}"
        )
    caps = _capabilities(client)
    if caps:
        lines.append(f"  capabilities: {sorted(caps.keys())}")
    if stderr_text:
        lines.append("  stderr:")
        for line in stderr_text.splitlines()[-40:]:
            lines.append(f"    {line}")
    else:
        lines.append("  stderr: (none captured, or process still running)")

    body = "\n".join(lines)
    _emit(session, body, allow_prompt)
    return CommandResult(
        ok=True,
        message=body,
        data={
            "server": name,
            "status": _client_status(client),
            "stderr": stderr_text,
            "server_info": info,
            "capabilities": caps,
        },
    )


@command(
    "/mcp",
    help="Manage MCP servers: list, status, reload, debug <server>.",
)
def mcp_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()
    if not raw:
        return _list(session, allow_prompt)

    head, _, rest = raw.partition(" ")
    sub = head.lower()
    rest = rest.strip()

    if sub == "list":
        return _list(session, allow_prompt)
    if sub == "status":
        return _status(session, allow_prompt)
    if sub == "reload":
        return _reload(session, allow_prompt)
    if sub == "debug":
        return _debug(session, rest, allow_prompt)

    return CommandResult(
        ok=False,
        message=f"Unknown subcommand {sub!r}. Usage: /mcp [list|status|reload|debug <server>]",
    )
