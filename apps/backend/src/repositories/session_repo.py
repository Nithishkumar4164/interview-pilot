"""Repository for InterviewSession entities."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.session import InterviewSession
from src.repositories.base import BaseRepository


class SessionRepository(BaseRepository[InterviewSession]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(InterviewSession, db)

    async def get_sessions_for_user(self, user_id: uuid.UUID) -> list[InterviewSession]:
        """Get all sessions for a user, ordered by creation time."""
        stmt = (
            select(InterviewSession)
            .where(InterviewSession.user_id == user_id)
            .order_by(InterviewSession.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
