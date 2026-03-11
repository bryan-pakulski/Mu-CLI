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
    usage_totals: dict[str, float] | None = None
    turns: list[dict] | None = None
    uploads: list[dict] | None = None
    research_artifacts: dict | None = None
    agentic_planning: bool | None = None
    research_mode: bool | None = None
    max_runtime_seconds: int | None = None
    condense_enabled: bool | None = None
    condense_window: int | None = None
    summary_index: list[dict] | None = None
    enabled_skills: list[str] | None = None
    traces: list[str] | None = None
    ollama_endpoint: str | None = None


class SessionStore:
    def __init__(self, root_dir: Path, session_name: str) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.session_name = session_name

    @property
    def path(self) -> Path:
        return self.root_dir / f"{self.session_name}.json"

    def use(self, session_name: str) -> None:
        self.session_name = session_name

    def load(self) -> SessionState | None:
        if not self.path.exists():
            return None
        raw = self.path.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
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
            usage_totals=payload.get("usage_totals"),
            turns=payload.get("turns"),
            uploads=payload.get("uploads"),
            research_artifacts=payload.get("research_artifacts"),
            agentic_planning=payload.get("agentic_planning"),
            research_mode=payload.get("research_mode"),
            max_runtime_seconds=payload.get("max_runtime_seconds"),
            condense_enabled=payload.get("condense_enabled"),
            condense_window=payload.get("condense_window"),
            summary_index=payload.get("summary_index"),
            enabled_skills=payload.get("enabled_skills"),
            traces=payload.get("traces"),
            ollama_endpoint=payload.get("ollama_endpoint"),
        )

    def save(self, state: SessionState) -> None:
        payload = {
            "provider": state.provider,
            "model": state.model,
            "workspace": state.workspace,
            "approval_mode": state.approval_mode,
            "messages": [asdict(message) for message in state.messages],
            "usage_totals": state.usage_totals or {},
            "turns": state.turns or [],
            "uploads": state.uploads or [],
            "research_artifacts": state.research_artifacts or {},
            "agentic_planning": state.agentic_planning,
            "research_mode": state.research_mode,
            "max_runtime_seconds": state.max_runtime_seconds,
            "condense_enabled": state.condense_enabled,
            "condense_window": state.condense_window,
            "summary_index": state.summary_index or [],
            "enabled_skills": state.enabled_skills or [],
            "traces": state.traces or [],
            "ollama_endpoint": state.ollama_endpoint,
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_sessions(self) -> list[str]:
        return sorted(path.stem for path in self.root_dir.glob("*.json"))

    def delete(self, session_name: str) -> bool:
        target = self.root_dir / f"{session_name}.json"
        if not target.exists():
            return False
        target.unlink()
        return True
