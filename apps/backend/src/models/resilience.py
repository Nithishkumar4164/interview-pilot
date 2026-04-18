import uuid

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import BaseModel


class SessionSnapshot(BaseModel):
    __tablename__ = "session_snapshots"
    __table_args__ = (Index("ix_session_snapshots_session_created", "session_id", "created_at"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=False
    )
    snapshot_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LLMProviderConfig(BaseModel):
    __tablename__ = "llm_provider_configs"

    task_type: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    primary_provider: Mapped[str] = mapped_column(String(30), nullable=False)
    primary_model: Mapped[str] = mapped_column(String(100), nullable=False)
    fallback_provider: Mapped[str] = mapped_column(String(30), nullable=False)
    fallback_model: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class LLMFailoverLog(BaseModel):
    __tablename__ = "llm_failover_log"

    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_sessions.id"), nullable=True
    )
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    from_provider: Mapped[str] = mapped_column(String(30), nullable=False)
    to_provider: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
