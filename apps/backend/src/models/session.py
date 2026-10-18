import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import BaseModel


class InterviewSession(BaseModel):
    __tablename__ = "interview_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="planning")
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    focus_areas: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    job_description: Mapped[str | None] = mapped_column(String, nullable=True)
    custom_notes: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_name: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_email: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_role: Mapped[str | None] = mapped_column(String, nullable=True)
    resume_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    meeting_url: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    plans: Mapped[list["InterviewPlan"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    transcripts: Mapped[list["Transcript"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    hint_events: Mapped[list["HintEvent"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    questions: Mapped[list["SessionQuestion"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="SessionQuestion.position"
    )
    reports: Mapped[list["Report"]] = relationship(back_populates="session", cascade="all, delete-orphan")


# Avoid circular imports — these are resolved by SQLAlchemy's string-based relationship lookup
from src.models.hint import HintEvent  # noqa: E402, F401
from src.models.plan import InterviewPlan  # noqa: E402, F401
from src.models.question import SessionQuestion  # noqa: E402, F401
from src.models.report import Report  # noqa: E402, F401
from src.models.transcript import Transcript  # noqa: E402, F401
