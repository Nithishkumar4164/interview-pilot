from collections.abc import AsyncIterator
from datetime import UTC, datetime

from opentelemetry.trace import Span

from src.llm.config import DEFAULT_TASK_MAPPINGS, TaskProviderMapping
from src.llm.factory import ProviderFactory
from src.llm.health import HealthMonitor
from src.llm.interface import (
    AnalysisContext,
    ILLMProvider,
    LLMConfig,
    StructuredOutput,
    TaskType,
)
from src.utils.logging import get_logger
from src.utils.tracing import get_tracer

logger = get_logger("llm.failover")
tracer = get_tracer("llm.failover")


def _record_result(span: Span, result: StructuredOutput) -> None:
    span.set_attribute("llm.provider", result.provider)
    span.set_attribute("llm.model", result.model)
    span.set_attribute("llm.input_tokens", result.input_tokens)
    span.set_attribute("llm.output_tokens", result.output_tokens)
    span.set_attribute("llm.latency_ms", result.latency_ms)


class FailoverManager:
    """Routes LLM calls through the appropriate provider with automatic failover.

    - Maps TaskType → (primary, fallback) provider
    - Uses HealthMonitor to detect failures
    - Falls back to secondary provider automatically
    - Logs all failover events
    """

    def __init__(self, health_monitor: HealthMonitor) -> None:
        self.health_monitor = health_monitor
        self._mappings: dict[TaskType, TaskProviderMapping] = dict(DEFAULT_TASK_MAPPINGS)
        self._provider_cache: dict[str, ILLMProvider] = {}
        self._failover_log: list[dict] = []

    def update_mapping(self, mapping: TaskProviderMapping) -> None:
        """Hot-reload a task mapping (e.g., from DB config change)."""
        self._mappings[mapping.task_type] = mapping
        # Invalidate cached providers for this mapping
        key_primary = f"{mapping.primary_provider}:{mapping.primary_model}"
        key_fallback = f"{mapping.fallback_provider}:{mapping.fallback_model}"
        self._provider_cache.pop(key_primary, None)
        self._provider_cache.pop(key_fallback, None)

    def _get_provider(self, provider_type: str, model: str) -> ILLMProvider:
        cache_key = f"{provider_type}:{model}"
        if cache_key not in self._provider_cache:
            provider = ProviderFactory.create(provider_type, model)
            self._provider_cache[cache_key] = provider
            self.health_monitor.register_provider(provider_type, provider)
        return self._provider_cache[cache_key]

    def _select_provider(self, task_type: TaskType) -> tuple[ILLMProvider, bool]:
        """Select the best available provider for a task.

        Returns (provider, is_failover).
        """
        mapping = self._mappings.get(task_type)
        if mapping is None:
            raise ValueError(f"No provider mapping for task type: {task_type}")

        # Try primary
        if self.health_monitor.is_available(mapping.primary_provider):
            return self._get_provider(mapping.primary_provider, mapping.primary_model), False

        # Failover to secondary
        logger.warning(
            "provider_failover",
            task=task_type.value,
            from_provider=mapping.primary_provider,
            to_provider=mapping.fallback_provider,
        )
        self._failover_log.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "task_type": task_type.value,
                "from_provider": mapping.primary_provider,
                "to_provider": mapping.fallback_provider,
                "reason": "primary_unavailable",
            }
        )
        return self._get_provider(mapping.fallback_provider, mapping.fallback_model), True

    async def analyze(
        self,
        context: AnalysisContext,
        config: LLMConfig | None = None,
        session_id: str | None = None,
    ) -> StructuredOutput:
        """Run an analysis call with automatic failover."""
        if config is None:
            config = LLMConfig()

        # Metadata only: prompts carry candidate PII and never go on spans.
        with tracer.start_as_current_span(
            "llm.analyze",
            attributes={
                "llm.task_type": context.task_type.value,
                "llm.max_tokens": config.max_tokens,
                "session.id": session_id or "",
            },
        ) as span:
            # start_as_current_span records the exception and sets ERROR status.
            return await self._analyze_with_failover(context, config, span)

    async def _analyze_with_failover(
        self,
        context: AnalysisContext,
        config: LLMConfig,
        span: Span,
    ) -> StructuredOutput:
        provider, is_failover = self._select_provider(context.task_type)
        span.set_attribute("llm.failover", is_failover)
        try:
            result = await provider.analyze(context, config)
            self.health_monitor.record_success(provider.provider_name, result.latency_ms)
            _record_result(span, result)
            return result
        except Exception as e:
            self.health_monitor.record_failure(provider.provider_name)
            logger.error(
                "llm_call_failed",
                provider=provider.provider_name,
                task=context.task_type.value,
                error=str(e),
            )
            # If we already failed over, re-raise
            if is_failover:
                raise
            # Try failover
            mapping = self._mappings[context.task_type]
            fallback = self._get_provider(mapping.fallback_provider, mapping.fallback_model)
            self._failover_log.append(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "task_type": context.task_type.value,
                    "from_provider": mapping.primary_provider,
                    "to_provider": mapping.fallback_provider,
                    "reason": f"primary_error: {e!s}",
                }
            )
            span.add_event(
                "llm.failover",
                {
                    "from_provider": mapping.primary_provider,
                    "to_provider": mapping.fallback_provider,
                    "reason": type(e).__name__,
                },
            )
            span.set_attribute("llm.failover", True)
            result = await fallback.analyze(context, config)
            self.health_monitor.record_success(fallback.provider_name, result.latency_ms)
            _record_result(span, result)
            return result

    async def complete(
        self,
        prompt: str,
        task_type: TaskType,
        config: LLMConfig | None = None,
    ) -> AsyncIterator[str]:
        """Stream a completion with automatic failover."""
        if config is None:
            config = LLMConfig(stream=True)

        provider, _ = self._select_provider(task_type)
        async for chunk in provider.complete(prompt, config):
            yield chunk

    def get_failover_log(self) -> list[dict]:
        return list(self._failover_log)

    def clear_failover_log(self) -> None:
        self._failover_log.clear()
