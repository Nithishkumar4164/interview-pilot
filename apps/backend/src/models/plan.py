import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import BaseModel


class InterviewPlan(BaseModel):
    __tablename__ = "interview_plans"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=False, index=True
    )
    plan_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    session: Mapped["InterviewSession"] = relationship(back_populates="plans")


class QuestionBank(BaseModel):
    __tablename__ = "question_bank"

    text: Mapped[str] = mapped_column(String, nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    tags: Mapped[list[str]] = mapped_column(
        type_=JSON, nullable=False, default=list
    )
    expected_time_min: Mapped[int] = mapped_column(Integer, nullable=False)
    optimal_approaches: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    common_pitfalls: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    hints: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    alternates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_retired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


from src.models.session import InterviewSession  # noqa: E402, F401
