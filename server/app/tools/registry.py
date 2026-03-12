from dataclasses import dataclass


@dataclass
class ToolDefinition:
    name: str
    description: str
    risk_level: str
    requires_approval: bool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {
            "workspace.read_file": ToolDefinition(
                name="workspace.read_file",
                description="Read a file from workspace",
                risk_level="low",
                requires_approval=False,
            ),
            "workspace.write_file": ToolDefinition(
                name="workspace.write_file",
                description="Write a file in workspace",
                risk_level="medium",
                requires_approval=True,
            ),
            "shell.exec": ToolDefinition(
                name="shell.exec",
                description="Execute shell command",
                risk_level="high",
                requires_approval=True,
            ),
        }

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)


tool_registry = ToolRegistry()
