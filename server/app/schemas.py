from datetime import datetime

from pydantic import BaseModel, Field

from server.app.persistence.models import ApprovalState, JobState, SessionStatus
from server.app.workspace.discovery import WorkspaceStore

class SessionCreate(BaseModel):
    workspace_path: str | None = None
    name: str = "default"
    mode: str = "interactive"
    provider_preferences: dict = Field(default_factory=lambda: {"ordered": ["ollama"]})
    policy_profile: str = "default"
    max_timeout_s: int = 300
    max_context_messages: int = 40
    max_context_chars: int = 8000
    max_stage_turns: int = 3


class SessionRead(BaseModel):
    id: str
    name: str
    workspace: WorkspaceStore | None = None
    mode: str
    provider_preferences: dict
    policy_profile: str
    context_state: dict
    status: SessionStatus
    created_at: datetime


class SessionUpdate(BaseModel):
    name: str | None = None
    workspace_path: str | None = None
    mode: str | None = None
    provider_preferences: dict | None = None
    policy_profile: str | None = None
    max_timeout_s: int | None = None
    max_context_messages: int | None = None
    max_context_chars: int | None = None
    max_stage_turns: int | None = None
    agentic_planning: bool | None = None
    research_mode: bool | None = None
    auto_condense: bool | None = None
    system_prompt_override: str | None = None
    rules_checklist: str | None = None


class JobCreate(BaseModel):
    goal: str
    constraints: dict = Field(default_factory=dict)
    acceptance_criteria: dict = Field(default_factory=dict)


class JobRead(BaseModel):
    id: str
    session_id: str
    goal: str
    constraints: dict
    acceptance_criteria: dict
    state: JobState
    checkpoints: dict
    result_artifacts: dict
    created_at: datetime


class EventRead(BaseModel):
    id: str
    session_id: str
    job_id: str | None
    event_type: str
    payload: dict
    created_at: datetime


class ApprovalRead(BaseModel):
    id: str
    session_id: str
    job_id: str
    tool_name: str
    reason: str
    state: ApprovalState
    created_at: datetime


class ApprovalDecisionWrite(BaseModel):
    decision: ApprovalState


class ProviderRead(BaseModel):
    name: str
    supports_streaming: bool
    supports_tools: bool
    supports_thinking: bool


class ToolRead(BaseModel):
    name: str
    description: str
    risk_level: str
    requires_approval: bool


class PolicyDecisionRead(BaseModel):
    tool_name: str
    decision: str
    reason: str


class StreamEvent(BaseModel):
    event_type: str
    session_id: str
    job_id: str | None = None
    payload: dict


class SkillRead(BaseModel):
    name: str
    description: str
    file_path: str




class EnabledItemsUpdate(BaseModel):
    enabled: list[str] = Field(default_factory=list)


class ToolConfigRead(ToolRead):
    enabled: bool


class SkillConfigRead(SkillRead):
    enabled: bool


class SkillContentRead(BaseModel):
    name: str
    file_path: str
    content: str


class SkillContentUpdate(BaseModel):
    content: str

class WorkspaceIndexRead(BaseModel):
    path: str
    file_type: str
    language: str
    last_modified: int
    content_hash: str
    description: str
    key_symbols: list
    tags: list
    priority_score: int


class WorkspaceIndexBuildResponse(BaseModel):
    session_id: str
    indexed_files: int


class WorkspaceIndexRefreshResponse(BaseModel):
    session_id: str
    indexed_files: int
    added: int
    updated: int
    removed: int
    next_refresh_after_s: int
