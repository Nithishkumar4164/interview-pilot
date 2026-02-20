from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.llm import FailoverManager, HealthMonitor

engine = create_async_engine(settings.database_url, echo=settings.environment == "development")
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

# Global singletons — avoids circular import from main.py
health_monitor = HealthMonitor()
failover_manager = FailoverManager(health_monitor)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_failover_manager() -> FailoverManager:
    """FastAPI dependency for the LLM failover manager."""
    return failover_manager
