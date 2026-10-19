import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import BaseModel


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
