from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ApprovalPolicy:
    mode: str = "ask"  # ask|auto|deny

    def should_approve(self, tool_name: str, args: dict) -> bool:
        if self.mode == "auto":
            return True
        if self.mode == "deny":
            return False

        response = input(f"Approve mutating tool `{tool_name}` with args {args}? [y/N]: ").strip().lower()
        return response in {"y", "yes"}
