import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import anthropic

from src.config import settings
from src.llm.interface import (
    AnalysisContext,
    ILLMProvider,
    LLMConfig,
    ProviderHealthStatus,
    ProviderStatus,
    StructuredOutput,
)
from src.utils.logging import get_logger

logger = get_logger("llm.anthropic")


def _strip_code_fences(text: str) -> str:
    """Strip markdown code fences (```json ... ```) from LLM output."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = stripped.index("\n")
        stripped = stripped[first_newline + 1:]
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    return stripped.strip()


class AnthropicProvider(ILLMProvider):
    """Claude implementation of ILLMProvider."""

    def __init__(self, model: str = "claude-sonnet-4-20250514") -> None:
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def complete(self, prompt: str, config: LLMConfig) -> AsyncIterator[str]:
        start = time.monotonic()
        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for text in stream.text_stream:
                    yield text

            latency = (time.monotonic() - start) * 1000
            logger.info(
                "llm_complete",
                provider="anthropic",
                model=self.model,
                latency_ms=round(latency, 1),
            )
        except anthropic.APIError as e:
            logger.error("llm_complete_error", provider="anthropic", error=str(e))
            raise

    async def analyze(self, context: AnalysisContext, config: LLMConfig) -> StructuredOutput:
        start = time.monotonic()
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                system=context.system_prompt,
                messages=[{"role": "user", "content": context.user_content}],
            )
            latency = (time.monotonic() - start) * 1000
            content = response.content[0].text if response.content else ""

            parsed = None
            if config.response_format == "json":
                try:
                    parsed = json.loads(_strip_code_fences(content))
                except json.JSONDecodeError:
                    logger.warning("llm_json_parse_failed", provider="anthropic", content=content[:200])

            logger.info(
                "llm_analyze",
                provider="anthropic",
                model=self.model,
                task=context.task_type.value,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=round(latency, 1),
            )

            return StructuredOutput(
                content=content,
                parsed=parsed,
                model=self.model,
                provider="anthropic",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=latency,
            )
        except anthropic.APIError as e:
            logger.error("llm_analyze_error", provider="anthropic", error=str(e))
            raise

    async def health_check(self) -> ProviderStatus:
        start = time.monotonic()
        try:
            await self.client.messages.create(
                model=self.model,
                max_tokens=5,
                messages=[{"role": "user", "content": "ping"}],
            )
            latency = (time.monotonic() - start) * 1000
            return ProviderStatus(
                provider="anthropic",
                status=ProviderHealthStatus.HEALTHY,
                last_check_at=datetime.now(UTC).isoformat(),
                average_latency_ms=latency,
            )
        except Exception as e:
            return ProviderStatus(
                provider="anthropic",
                status=ProviderHealthStatus.DOWN,
                last_check_at=datetime.now(UTC).isoformat(),
                consecutive_failures=1,
            )
