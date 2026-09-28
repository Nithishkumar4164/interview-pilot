"""Repository for HintEvent entities."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.hint import HintEvent
from src.repositories.base import BaseRepository


class HintEventRepository(BaseRepository[HintEvent]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(HintEvent, db)

    async def get_session_hints(
        self,
        session_id: uuid.UUID,
        limit: int = 100,
    ) -> list[HintEvent]:
        """Get hint events for a session, ordered by display time."""
        stmt = select(HintEvent).where(HintEvent.session_id == session_id).order_by(HintEvent.displayed_at).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_acknowledgment(
        self,
        hint_id: uuid.UUID,
        session_id: uuid.UUID,
        interviewer_used: bool,
        candidate_response_quality: str,
        time_to_respond_ms: int | None = None,
    ) -> bool:
        """Update a hint event with acknowledgment data by exact ID, within one session."""
        stmt = (
            update(HintEvent)
            .where(HintEvent.id == hint_id, HintEvent.session_id == session_id)
            .values(
                interviewer_used=interviewer_used,
                candidate_response_quality=candidate_response_quality,
                time_to_respond_ms=time_to_respond_ms,
            )
        )
        result = await self.db.execute(stmt)
        return result.rowcount > 0

    async def acknowledge_latest_unacked(
        self,
        session_id: uuid.UUID,
        interviewer_used: bool,
        candidate_response_quality: str,
        time_to_respond_ms: int | None = None,
    ) -> bool:
        """Find the most recent unacknowledged hint for a session and update it.

        Falls back to this when the frontend-generated hint_id doesn't match
        the backend-generated ID stored in the DB.
        """
        # Find latest unacknowledged hint
        stmt = (
            select(HintEvent.id)
            .where(HintEvent.session_id == session_id)
            .where(HintEvent.interviewer_used == False)  # noqa: E712
            .order_by(HintEvent.displayed_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return False

        update_stmt = (
            update(HintEvent)
            .where(HintEvent.id == row)
            .values(
                interviewer_used=interviewer_used,
                candidate_response_quality=candidate_response_quality,
                time_to_respond_ms=time_to_respond_ms,
            )
        )
        await self.db.execute(update_stmt)
        return True
