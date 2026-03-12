from datetime import datetime

from pydantic import BaseModel, Field

from server.app.persistence.models import ApprovalState, JobState, SessionStatus


class SessionCreate(BaseModel):
    workspace_path: str
    mode: str = "interactive"
    provider_preferences: dict = Field(default_factory=lambda: {"ordered": ["ollama", "mock"]})
    policy_profile: str = "default"


class SessionRead(BaseModel):
    id: str
    workspace_path: str
    mode: str
    provider_preferences: dict
    policy_profile: str
    context_state: dict
    status: SessionStatus
    created_at: datetime


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
