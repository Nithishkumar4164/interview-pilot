"""Repository for CodeSnapshot entities."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.analysis import CodeSnapshot
from src.repositories.base import BaseRepository


class CodeSnapshotRepository(BaseRepository[CodeSnapshot]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(CodeSnapshot, db)

    async def get_session_snapshots(
        self,
        session_id: uuid.UUID,
        limit: int = 100,
    ) -> list[CodeSnapshot]:
        """Get code snapshots for a session, ordered by timestamp."""
        stmt = (
            select(CodeSnapshot)
            .where(CodeSnapshot.session_id == session_id)
            .order_by(CodeSnapshot.timestamp)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_latest(self, session_id: uuid.UUID) -> CodeSnapshot | None:
        """Get the latest code snapshot for a session."""
        stmt = (
            select(CodeSnapshot)
            .where(CodeSnapshot.session_id == session_id)
            .order_by(CodeSnapshot.timestamp.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
