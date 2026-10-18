import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import BaseModel

# QuestionBank model is defined in plan.py to keep all planning models together
from src.models.plan import QuestionBank  # noqa: F401


class SessionQuestion(BaseModel):
    """A question the interviewer asked in a session: the interview's questionnaire.

    Drafts the interviewer rejected are never stored. ``question`` holds the
    question as the dashboard uses it (text, hints, approaches, pitfalls); its
    "id" is this row's id.
    """

    __tablename__ = "session_questions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    topic: Mapped[str | None] = mapped_column(String, nullable=True)
    question: Mapped[dict] = mapped_column(JSON, nullable=False)
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    session: Mapped["InterviewSession"] = relationship(back_populates="questions")


from src.models.session import InterviewSession  # noqa: E402, F401
