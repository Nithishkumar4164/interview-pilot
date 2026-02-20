from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.llm import FailoverManager, HealthMonitor
from src.llm.config import DEFAULT_TASK_MAPPINGS, TaskProviderMapping

engine = create_async_engine(settings.database_url, echo=settings.environment == "development")
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

_has_anthropic = bool(settings.anthropic_api_key)
_has_openai = bool(settings.openai_api_key)

# If no LLM keys, fall back to mock provider for development
if not _has_anthropic and not _has_openai:
    import logging
    logging.getLogger("dependencies").warning(
        "No LLM API keys configured — activating mock provider mode. "
        "Set INTERVIEW_PILOT_ANTHROPIC_API_KEY and/or INTERVIEW_PILOT_OPENAI_API_KEY for real analysis."
    )

# Global singletons — avoids circular import from main.py
health_monitor = HealthMonitor()
failover_manager = FailoverManager(health_monitor)

# Override mappings so only providers with valid keys are used.
# If no keys at all, use mock provider. If only one key, use that provider for both.
if not _has_anthropic and not _has_openai:
    for task_type in DEFAULT_TASK_MAPPINGS:
        failover_manager.update_mapping(
            TaskProviderMapping(
                task_type=task_type,
                primary_provider="mock",
                primary_model="mock-v1",
                fallback_provider="mock",
                fallback_model="mock-v1",
            )
        )
elif not _has_anthropic or not _has_openai:
    for task_type, mapping in DEFAULT_TASK_MAPPINGS.items():
        if _has_anthropic:
            provider, model = mapping.primary_provider, mapping.primary_model
        else:
            provider, model = mapping.fallback_provider, mapping.fallback_model
        failover_manager.update_mapping(
            TaskProviderMapping(
                task_type=task_type,
                primary_provider=provider,
                primary_model=model,
                fallback_provider=provider,
                fallback_model=model,
            )
        )


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
