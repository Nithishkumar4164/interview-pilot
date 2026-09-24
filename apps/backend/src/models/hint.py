import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import BaseModel


class HintEvent(BaseModel):
    __tablename__ = "hint_events"
    __table_args__ = (
        Index("ix_hint_events_session_question", "session_id", "question_id"),
        Index("ix_hint_events_session_displayed", "session_id", "displayed_at"),
        Index("uq_hint_events_idempotency_key", "idempotency_key", unique=True),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=False
    )
    # Deterministic key from the producer; a re-emitted event is skipped on insert.
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    question_id: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    displayed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interviewer_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    candidate_response_quality: Mapped[str] = mapped_column(String(10), nullable=False, default="none")
    time_to_respond_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    session: Mapped["InterviewSession"] = relationship(back_populates="hint_events")


from src.models.session import InterviewSession  # noqa: E402, F401
