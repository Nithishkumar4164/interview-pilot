"""Repository for AnalysisEvent entities."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.analysis import AnalysisEvent
from src.repositories.base import BaseRepository


class AnalysisEventRepository(BaseRepository[AnalysisEvent]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(AnalysisEvent, db)

    async def get_session_events(
        self,
        session_id: uuid.UUID,
        limit: int = 200,
        offset: int = 0,
    ) -> list[AnalysisEvent]:
        """Get analysis events for a session, ordered by creation time."""
        stmt = (
            select(AnalysisEvent)
            .where(AnalysisEvent.session_id == session_id)
            .order_by(AnalysisEvent.created_at)
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_recent(
        self,
        session_id: uuid.UUID,
        count: int = 20,
    ) -> list[AnalysisEvent]:
        """Get the most recent analysis events for a session."""
        stmt = (
            select(AnalysisEvent)
            .where(AnalysisEvent.session_id == session_id)
            .order_by(AnalysisEvent.created_at.desc())
            .limit(count)
        )
        result = await self.db.execute(stmt)
        return list(reversed(result.scalars().all()))
