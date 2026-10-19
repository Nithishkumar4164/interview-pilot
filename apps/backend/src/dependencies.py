from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.graphs.checkpointer import Checkpointer
from src.llm import FailoverManager, HealthMonitor
from src.llm.config import DEFAULT_TASK_MAPPINGS, TaskProviderMapping

engine = create_async_engine(settings.database_url, echo=settings.environment == "development")
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

_has_anthropic = bool(settings.anthropic_api_key)
_has_openai = bool(settings.openai_api_key)
_use_claude_code = settings.dev_llm_provider == "claude_code"

# If no LLM keys, fall back to mock provider for development
if not _has_anthropic and not _has_openai and not _use_claude_code:
    import logging

    logging.getLogger("dependencies").warning(
        "No LLM API keys configured — activating mock provider mode. "
        "Set INTERVIEW_PILOT_ANTHROPIC_API_KEY and/or INTERVIEW_PILOT_OPENAI_API_KEY for real analysis."
    )

# Global singletons — avoids circular import from main.py
health_monitor = HealthMonitor()
failover_manager = FailoverManager(health_monitor)
checkpointer = Checkpointer()  # opened in main.lifespan

# Override mappings so only providers with valid keys are used.
# If no keys at all, use mock provider. If only one key, use that provider for both.
if _use_claude_code:
    # Every task on the signed-in Claude Code CLI with its usual Claude model; any API keys are ignored.
    from src.llm.factory import ProviderFactory
    from src.llm.providers.claude_code import ClaudeCodeProvider

    ProviderFactory.register("claude_code", ClaudeCodeProvider)
    for task_type, mapping in DEFAULT_TASK_MAPPINGS.items():
        model = mapping.primary_model if mapping.primary_provider == "anthropic" else mapping.fallback_model
        failover_manager.update_mapping(
            TaskProviderMapping(
                task_type=task_type,
                primary_provider="claude_code",
                primary_model=model,
                fallback_provider="claude_code",
                fallback_model=model,
            )
        )
elif not _has_anthropic and not _has_openai:
    _dev_provider, _dev_model = "mock", "mock-v1"
    if settings.dev_llm_provider == "scripted":
        # Deterministic, input-sensitive answers (red flags, contradictions, tool use) for local e2e runs.
        from src.llm.factory import ProviderFactory
        from src.replay.scripted_provider import ScriptedProvider

        ProviderFactory.register("scripted", ScriptedProvider)
        _dev_provider, _dev_model = "scripted", "scripted-v1"
    for task_type in DEFAULT_TASK_MAPPINGS:
        failover_manager.update_mapping(
            TaskProviderMapping(
                task_type=task_type,
                primary_provider=_dev_provider,
                primary_model=_dev_model,
                fallback_provider=_dev_provider,
                fallback_model=_dev_model,
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


def get_checkpointer() -> Checkpointer:
    """FastAPI dependency for the LangGraph Postgres checkpointer."""
    return checkpointer


def get_persistence_service():
    """FastAPI dependency for the persistence service.

    Lazy import to avoid circular dependency with main.py.
    """
    from src.main import persistence_service

    return persistence_service


def get_report_runner():
    """The process-wide ReportRunner (ReportGraph on the shared checkpointer)."""
    global _report_runner
    if _report_runner is None:
        from src.graphs.report_graph import ReportRunner

        _report_runner = ReportRunner(failover_manager, async_session_factory, checkpointer)
    return _report_runner


_report_runner = None
