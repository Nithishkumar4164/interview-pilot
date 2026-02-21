import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import BaseModel


class AnalysisEvent(BaseModel):
    __tablename__ = "analysis_events"
    __table_args__ = (
        Index("ix_analysis_events_session_created", "session_id", "created_at"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=False
    )
    transcript_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transcripts.id"), nullable=True
    )
    answer_quality: Mapped[int] = mapped_column(Integer, nullable=False)
    on_track: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    insight: Mapped[str] = mapped_column(String, nullable=False)
    red_flags: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(String, nullable=True)
    hint_signal: Mapped[str | None] = mapped_column(String, nullable=True)
    dimensions: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class CodeSnapshot(BaseModel):
    __tablename__ = "code_snapshots"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str] = mapped_column(String(30), nullable=False)
    diff: Mapped[str | None] = mapped_column(String, nullable=True)
    approach: Mapped[str | None] = mapped_column(String(100), nullable=True)
    progress_state: Mapped[str] = mapped_column(String(20), nullable=False, default="empty")
    timestamp: Mapped[str] = mapped_column(String, nullable=False)
