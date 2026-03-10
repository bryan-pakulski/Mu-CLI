from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mu_cli.agent import Agent
from mu_cli.pricing import PricingCatalog
from mu_cli.session import SessionStore
from mu_cli.skills import SkillStore
from mu_cli.tools.base import Tool
from mu_cli.workspace import WorkspaceStore


@dataclass(slots=True)
class WebRuntime:
    provider: str
    model: str
    openai_api_key: str | None
    google_api_key: str | None
    approval_mode: str
    system_prompt: str
    session_name: str
    workspace_path: str | None
    debug: bool
    agentic_planning: bool
    research_mode: bool
    workspace_store: WorkspaceStore
    session_store: SessionStore
    pricing: PricingCatalog
    tools: list[Tool]
    agent: Agent
    traces: list[str]
    session_usage: dict[str, float]
    session_turns: list[dict]
    uploads: list[dict]
    uploads_dir: Path
    base_tools: list[Tool]
    enabled_tools: dict[str, bool]
    custom_tool_specs: list[dict]
    custom_tool_errors: list[str]
    research_artifacts: dict[str, Any]
    approval_condition: Any = field(default_factory=lambda: __import__("threading").Condition())
    pending_approval: dict[str, Any] | None = None
    background_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    max_runtime_seconds: int = 900
    condense_enabled: bool = False
    condense_window: int = 12
    summary_index: list[dict[str, Any]] = field(default_factory=list)
    skill_store: SkillStore | None = None
    enabled_skills: list[str] = field(default_factory=list)


def default_usage() -> dict[str, float]:
    return {"input_tokens": 0.0, "output_tokens": 0.0, "total_tokens": 0.0, "estimated_cost_usd": 0.0}
