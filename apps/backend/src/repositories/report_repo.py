"""Repository for Report and ReportNote entities."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.report import Report, ReportNote
from src.repositories.base import BaseRepository


class ReportRepository(BaseRepository[Report]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(Report, db)

    async def get_by_session(self, session_id: uuid.UUID) -> Report | None:
        """Get the report for a session, including notes."""
        stmt = (
            select(Report)
            .where(Report.session_id == session_id)
            .options(selectinload(Report.notes))
            .order_by(Report.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_with_notes(self, report_id: uuid.UUID) -> Report | None:
        """Get a report by ID, including notes."""
        stmt = (
            select(Report)
            .where(Report.id == report_id)
            .options(selectinload(Report.notes))
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()


class ReportNoteRepository(BaseRepository[ReportNote]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(ReportNote, db)

    async def get_notes_for_report(self, report_id: uuid.UUID) -> list[ReportNote]:
        """Get all notes for a report."""
        stmt = (
            select(ReportNote)
            .where(ReportNote.report_id == report_id)
            .order_by(ReportNote.created_at)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
