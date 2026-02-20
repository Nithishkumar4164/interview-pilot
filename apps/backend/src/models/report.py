import uuid

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import BaseModel


class Report(BaseModel):
    __tablename__ = "reports"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=False, index=True
    )
    report_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence_level: Mapped[float] = mapped_column(Float, nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    session: Mapped["InterviewSession"] = relationship(back_populates="reports")
    notes: Mapped[list["ReportNote"]] = relationship(back_populates="report")


class ReportNote(BaseModel):
    __tablename__ = "report_notes"

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    note_text: Mapped[str] = mapped_column(String, nullable=False)

    report: Mapped["Report"] = relationship(back_populates="notes")


from src.models.session import InterviewSession  # noqa: E402, F401
