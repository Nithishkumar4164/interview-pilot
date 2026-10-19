"""Repository for InterviewSession entities."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

    async def get_sessions_enriched(
        self,
        user_id: uuid.UUID | None,
        *,
        status_filter: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[InterviewSession], int]:
        """Return a page of sessions with reports and plans eagerly loaded,
        plus the total row count.

        Pagination lives in SQL (``LIMIT``/``OFFSET``) so we don't hydrate
        every session just to slice it in Python — scans stay bounded as
        a user's history grows.
        """
        base = select(InterviewSession)
        if user_id is not None:  # None = all users (admin view)
            base = base.where(InterviewSession.user_id == user_id)
        if status_filter:
            base = base.where(InterviewSession.status == status_filter)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        page_stmt = (
            base.options(selectinload(InterviewSession.reports))
            .options(selectinload(InterviewSession.plans))
            .order_by(InterviewSession.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        page = (await self.db.execute(page_stmt)).scalars().unique().all()
        return list(page), total
