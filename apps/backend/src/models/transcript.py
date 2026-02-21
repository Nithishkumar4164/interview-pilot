import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import BaseModel


class Transcript(BaseModel):
    __tablename__ = "transcripts"
    __table_args__ = (
        Index("ix_transcripts_session_timestamp", "session_id", "timestamp"),
        Index("ix_transcripts_session_final", "session_id", "is_final"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=False
    )
    speaker_role: Mapped[str] = mapped_column(String(30), nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    timestamp: Mapped[str] = mapped_column(String, nullable=False)
    word_timestamps: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_retroactive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="tab")
    root_speaker_id: Mapped[str] = mapped_column(String, nullable=False, default="")
    speech_vector_id: Mapped[str] = mapped_column(String, nullable=False, default="")

    session: Mapped["InterviewSession"] = relationship(back_populates="transcripts")


from src.models.session import InterviewSession  # noqa: E402, F401
