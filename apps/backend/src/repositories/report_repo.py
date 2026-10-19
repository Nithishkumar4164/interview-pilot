"""Repository for Report and ReportNote entities."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.report import Report, ReportNote
from src.models.session import InterviewSession
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
        stmt = select(Report).where(Report.id == report_id).options(selectinload(Report.notes))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def _owned_by(stmt, owner_id: uuid.UUID | None):
        """Restrict to reports whose session belongs to owner_id (None = no restriction)."""
        if owner_id is None:
            return stmt
        return stmt.join(InterviewSession, InterviewSession.id == Report.session_id).where(
            InterviewSession.user_id == owner_id
        )

    async def list_reports(self, limit: int = 20, offset: int = 0, owner_id: uuid.UUID | None = None) -> list[Report]:
        """List reports ordered by creation date descending."""
        stmt = self._owned_by(select(Report), owner_id).order_by(Report.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_reports(self, owner_id: uuid.UUID | None = None) -> int:
        """Count total reports."""
        stmt = self._owned_by(select(func.count()).select_from(Report), owner_id)
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def get_multiple(self, report_ids: list[uuid.UUID], owner_id: uuid.UUID | None = None) -> list[Report]:
        """Fetch multiple reports by ID."""
        if not report_ids:
            return []
        stmt = (
            self._owned_by(select(Report), owner_id).where(Report.id.in_(report_ids)).order_by(Report.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())


class ReportNoteRepository(BaseRepository[ReportNote]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(ReportNote, db)

    async def get_notes_for_report(self, report_id: uuid.UUID) -> list[ReportNote]:
        """Get all notes for a report."""
        stmt = select(ReportNote).where(ReportNote.report_id == report_id).order_by(ReportNote.created_at)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
