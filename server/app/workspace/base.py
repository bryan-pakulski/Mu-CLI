from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class ToolResult:
    ok: bool
    output: str


class Tool(Protocol):
    name: str
    description: str
    schema: dict[str, Any]
    mutating: bool

    def run(self, args: dict[str, Any]) -> ToolResult:
        ...
