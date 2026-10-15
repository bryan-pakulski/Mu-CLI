"""Tool-registry slash command: /tool list|enable|disable."""

from typing import Any

from . import CommandResult, command


def _list_tools(session: Any, allow_prompt: bool) -> CommandResult:
    from mu.tools.descriptors import TOOLS

    if allow_prompt:
        ui = getattr(session, "ui", None)
        console = getattr(ui, "console", None) if ui is not None else None
        if console is not None:
            try:
                from rich import box
                from rich.table import Table

                table = Table(title="Available Tools", box=box.SIMPLE_HEAD, show_lines=True)
                table.add_column("Tool", style="wash.accent", no_wrap=True)
                table.add_column("Description", style="wash.ink", width=40)
                table.add_column("Parameters", style="wash.plum")
                table.add_column("Approval", style="wash.caution", justify="center")
                table.add_column("Status", style="wash.ok", justify="center")
                for tool in TOOLS:
                    status = (
                        "[wash.danger]OFF[/wash.danger]"
                        if tool.name in session.disabled_tools
                        else "[wash.ok]ON[/wash.ok]"
                    )
                    approval = "Yes" if tool.requires_approval else "No"
                    params = []
                    props = tool.parameters.get("properties", {})
                    required = tool.parameters.get("required", [])
                    for pname, pinfo in props.items():
                        star = "[wash.danger]*[/wash.danger]" if pname in required else ""
                        ptype = pinfo.get("type", "any")
                        params.append(f"{pname}{star} [wash.muted]({ptype})[/wash.muted]")
                    table.add_row(
                        tool.name,
                        tool.description,
                        "\n".join(params) if params else "None",
                        approval,
                        status,
                    )
                console.print(table)
                console.print("[wash.muted] [wash.danger]*[/wash.danger] indicates required parameter[/wash.muted]")
            except Exception:
                pass

    tools_data = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "requires_approval": tool.requires_approval,
            "enabled": tool.name not in session.disabled_tools,
        }
        for tool in TOOLS
    ]
    return CommandResult(
        ok=True, message=f"{len(tools_data)} tool(s).", data={"tools": tools_data}
    )


@command(
    "/tool",
    help="Manage tools: list, enable <name>, disable <name>.",
)
def tool_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()
    if not raw:
        return _list_tools(session, allow_prompt)

    head, _, rest = raw.partition(" ")
    sub = head.lower()
    name = rest.strip()

    if sub == "list":
        return _list_tools(session, allow_prompt)
    if sub == "disable":
        if not name:
            return CommandResult(ok=False, message="Usage: /tool disable <name>")
        if name not in session.disabled_tools:
            session.disabled_tools.append(name)
        return CommandResult(
            ok=True,
            message=f"Tool '{name}' disabled.",
            data={"disabled_tools": list(session.disabled_tools)},
        )
    if sub == "enable":
        if not name:
            return CommandResult(ok=False, message="Usage: /tool enable <name>")
        if name in session.disabled_tools:
            session.disabled_tools.remove(name)
        return CommandResult(
            ok=True,
            message=f"Tool '{name}' enabled.",
            data={"disabled_tools": list(session.disabled_tools)},
        )

    return CommandResult(
        ok=False,
        message="Usage: /tool <enable|disable|list> [toolname]",
    )
