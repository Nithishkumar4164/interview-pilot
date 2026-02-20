"""Repository for QuestionBank entities with filtering support."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.plan import QuestionBank
from src.repositories.base import BaseRepository


class QuestionRepository(BaseRepository[QuestionBank]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(QuestionBank, db)

    async def find_by_category(
        self,
        category: str,
        difficulty_min: int = 1,
        difficulty_max: int = 5,
        limit: int = 50,
    ) -> list[QuestionBank]:
        stmt = (
            select(QuestionBank)
            .where(
                QuestionBank.category == category,
                QuestionBank.difficulty >= difficulty_min,
                QuestionBank.difficulty <= difficulty_max,
                QuestionBank.is_retired == False,  # noqa: E712
            )
            .order_by(QuestionBank.difficulty)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def find_by_tags(
        self,
        tags: list[str],
        difficulty_min: int = 1,
        difficulty_max: int = 5,
        limit: int = 50,
    ) -> list[QuestionBank]:
        """Find questions that contain ANY of the given tags (JSON array overlap)."""
        stmt = (
            select(QuestionBank)
            .where(
                QuestionBank.difficulty >= difficulty_min,
                QuestionBank.difficulty <= difficulty_max,
                QuestionBank.is_retired == False,  # noqa: E712
            )
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        questions = result.scalars().all()
        # Filter by tags in Python (portable across dialects)
        tag_set = set(tags)
        return [q for q in questions if tag_set & set(q.tags or [])]

    async def find_by_id(self, question_id: uuid.UUID) -> QuestionBank | None:
        return await self.get_by_id(question_id)

    async def find_by_difficulty_range(
        self,
        difficulty_min: int,
        difficulty_max: int,
        category: str | None = None,
        exclude_ids: list[uuid.UUID] | None = None,
        limit: int = 50,
    ) -> list[QuestionBank]:
        conditions = [
            QuestionBank.difficulty >= difficulty_min,
            QuestionBank.difficulty <= difficulty_max,
            QuestionBank.is_retired == False,  # noqa: E712
        ]
        if category:
            conditions.append(QuestionBank.category == category)
        if exclude_ids:
            conditions.append(QuestionBank.id.notin_(exclude_ids))

        stmt = select(QuestionBank).where(*conditions).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
