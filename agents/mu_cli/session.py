from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from mu_cli.core.types import Message, Role


@dataclass(slots=True)
class SessionState:
    provider: str
    model: str
    workspace: str | None
    approval_mode: str
    messages: list[Message]


class SessionStore:
    def __init__(self, root_dir: Path, session_name: str) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.root_dir / f"{session_name}.json"

    def load(self) -> SessionState | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        messages = [
            Message(
                role=Role(item["role"]),
                content=item["content"],
                name=item.get("name"),
                metadata=item.get("metadata", {}),
            )
            for item in payload.get("messages", [])
        ]
        return SessionState(
            provider=payload.get("provider", "echo"),
            model=payload.get("model", "echo"),
            workspace=payload.get("workspace"),
            approval_mode=payload.get("approval_mode", "ask"),
            messages=messages,
        )

    def save(self, state: SessionState) -> None:
        payload = {
            "provider": state.provider,
            "model": state.model,
            "workspace": state.workspace,
            "approval_mode": state.approval_mode,
            "messages": [asdict(message) for message in state.messages],
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
