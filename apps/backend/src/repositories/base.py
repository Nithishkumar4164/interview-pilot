import uuid
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.base import BaseModel

T = TypeVar("T", bound=BaseModel)


class BaseRepository(Generic[T]):
    """Generic async repository providing CRUD operations."""

    def __init__(self, model: type[T], db: AsyncSession) -> None:
        self.model = model
        self.db = db

    async def get_by_id(self, id: uuid.UUID) -> T | None:
        result = await self.db.execute(select(self.model).where(self.model.id == id))
        return result.scalar_one_or_none()

    async def create(self, entity: T) -> T:
        self.db.add(entity)
        await self.db.flush()
        return entity

    async def update(self, entity: T) -> T:
        await self.db.merge(entity)
        await self.db.flush()
        return entity

    async def delete(self, entity: T) -> None:
        await self.db.delete(entity)
        await self.db.flush()

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[T]:
        result = await self.db.execute(select(self.model).limit(limit).offset(offset))
        return list(result.scalars().all())
