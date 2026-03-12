import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.app.persistence.db import Base


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


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    workspace_path: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, default="interactive")
    provider_preferences: Mapped[dict] = mapped_column(JSON().with_variant(SQLITE_JSON, "sqlite"))
    policy_profile: Mapped[str] = mapped_column(String, default="default")
    context_state: Mapped[dict] = mapped_column(JSON().with_variant(SQLITE_JSON, "sqlite"))
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus), default=SessionStatus.active)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    jobs: Mapped[list["JobModel"]] = relationship(back_populates="session", cascade="all, delete")


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
