import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.app.persistence.db import Base
from server.app.workspace.discovery import WorkspaceStore, WorkspaceStoreType

class SessionStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    completed = "completed"
    failed = "failed"


class JobState(str, enum.Enum):
    queued = "queued"
    running = "running"
    awaiting_approval = "awaiting_approval"
    blocked = "blocked"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ApprovalState(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    denied = "denied"


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace: Mapped[WorkspaceStore | None] = mapped_column(WorkspaceStoreType())
    mode: Mapped[str] = mapped_column(String, default="interactive")
    provider_preferences: Mapped[dict] = mapped_column(JSON().with_variant(SQLITE_JSON, "sqlite"))
    policy_profile: Mapped[str] = mapped_column(String, default="default")
    context_state: Mapped[dict] = mapped_column(JSON().with_variant(SQLITE_JSON, "sqlite"))
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus), default=SessionStatus.active)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    jobs: Mapped[list["JobModel"]] = relationship(back_populates="session", cascade="all, delete")

    @property
    def name(self) -> str:
        context_state = self.context_state or {}
        return context_state.get("name") or "default"

    @name.setter
    def name(self, value: str) -> None:
        context_state = dict(self.context_state or {})
        context_state["name"] = value
        context_state.setdefault("messages", [])
        context_state.setdefault("summary", None)
        context_state.setdefault("memory_refs", [])
        self.context_state = context_state


class JobModel(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    constraints: Mapped[dict] = mapped_column(
        JSON().with_variant(SQLITE_JSON, "sqlite"),
        default=dict,
    )
    acceptance_criteria: Mapped[dict] = mapped_column(
        JSON().with_variant(SQLITE_JSON, "sqlite"), default=dict
    )
    state: Mapped[JobState] = mapped_column(Enum(JobState), default=JobState.queued)
    checkpoints: Mapped[dict] = mapped_column(
        JSON().with_variant(SQLITE_JSON, "sqlite"),
        default=dict,
    )
    result_artifacts: Mapped[dict] = mapped_column(
        JSON().with_variant(SQLITE_JSON, "sqlite"),
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[SessionModel] = relationship(back_populates="jobs")


class EventModel(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON().with_variant(SQLITE_JSON, "sqlite"), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApprovalModel(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[ApprovalState] = mapped_column(Enum(ApprovalState), default=ApprovalState.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkspaceFileIndexModel(Base):
    __tablename__ = "workspace_file_index"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    file_type: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str] = mapped_column(String, nullable=False)
    last_modified: Mapped[int] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    key_symbols: Mapped[list] = mapped_column(
        JSON().with_variant(SQLITE_JSON, "sqlite"),
        default=list,
    )
    tags: Mapped[list] = mapped_column(
        JSON().with_variant(SQLITE_JSON, "sqlite"),
        default=list,
    )
    priority_score: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
