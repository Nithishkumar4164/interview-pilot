"""Repository for Transcript entities."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.transcript import Transcript
from src.repositories.base import BaseRepository


class TranscriptRepository(BaseRepository[Transcript]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(Transcript, db)

    async def get_session_transcripts(
        self,
        session_id: uuid.UUID,
        final_only: bool = False,
        limit: int = 500,
        offset: int = 0,
    ) -> list[Transcript]:
        """Get transcripts for a session, ordered by timestamp."""
        conditions = [Transcript.session_id == session_id]
        if final_only:
            conditions.append(Transcript.is_final == True)  # noqa: E712

        stmt = (
            select(Transcript)
            .where(*conditions)
            .order_by(Transcript.timestamp)
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_recent(
        self,
        session_id: uuid.UUID,
        count: int = 10,
        final_only: bool = True,
    ) -> list[Transcript]:
        """Get the most recent transcripts for a session."""
        conditions = [Transcript.session_id == session_id]
        if final_only:
            conditions.append(Transcript.is_final == True)  # noqa: E712

        stmt = (
            select(Transcript)
            .where(*conditions)
            .order_by(Transcript.timestamp.desc())
            .limit(count)
        )
        result = await self.db.execute(stmt)
        # Return in chronological order
        return list(reversed(result.scalars().all()))

    async def count_session_transcripts(
        self,
        session_id: uuid.UUID,
        final_only: bool = False,
    ) -> int:
        """Count transcripts for a session."""
        from sqlalchemy import func

        conditions = [Transcript.session_id == session_id]
        if final_only:
            conditions.append(Transcript.is_final == True)  # noqa: E712

        stmt = select(func.count()).select_from(Transcript).where(*conditions)
        result = await self.db.execute(stmt)
        return result.scalar() or 0
