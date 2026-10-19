"""Repository for InterviewPlan entities."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.plan import InterviewPlan
from src.repositories.base import BaseRepository


class PlanRepository(BaseRepository[InterviewPlan]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(InterviewPlan, db)

    async def get_active_plan(self, session_id: uuid.UUID) -> InterviewPlan | None:
        """Get the active plan for a session."""
        stmt = (
            select(InterviewPlan)
            .where(
                InterviewPlan.session_id == session_id,
                InterviewPlan.is_active == True,  # noqa: E712
            )
            .order_by(InterviewPlan.version.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_plans_for_session(self, session_id: uuid.UUID) -> list[InterviewPlan]:
        """Get all plans for a session, ordered by version."""
        stmt = (
            select(InterviewPlan).where(InterviewPlan.session_id == session_id).order_by(InterviewPlan.version.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def deactivate_all(self, session_id: uuid.UUID) -> None:
        """Deactivate all plans for a session (before creating a new one)."""
        stmt = select(InterviewPlan).where(
            InterviewPlan.session_id == session_id,
            InterviewPlan.is_active == True,  # noqa: E712
        )
        result = await self.db.execute(stmt)
        for plan in result.scalars().all():
            plan.is_active = False
        await self.db.flush()
