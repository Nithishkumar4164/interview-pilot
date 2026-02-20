import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import openai

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

logger = get_logger("llm.openai")


class OpenAIProvider(ILLMProvider):
    """GPT implementation of ILLMProvider."""

    def __init__(self, model: str = "gpt-4o") -> None:
        self.model = model
        self.client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    @property
    def provider_name(self) -> str:
        return "openai"

    async def complete(self, prompt: str, config: LLMConfig) -> AsyncIterator[str]:
        start = time.monotonic()
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

            latency = (time.monotonic() - start) * 1000
            logger.info(
                "llm_complete",
                provider="openai",
                model=self.model,
                latency_ms=round(latency, 1),
            )
        except openai.APIError as e:
            logger.error("llm_complete_error", provider="openai", error=str(e))
            raise

    async def analyze(self, context: AnalysisContext, config: LLMConfig) -> StructuredOutput:
        start = time.monotonic()
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                messages=[
                    {"role": "system", "content": context.system_prompt},
                    {"role": "user", "content": context.user_content},
                ],
            )
            latency = (time.monotonic() - start) * 1000
            content = response.choices[0].message.content or ""

            parsed = None
            if config.response_format == "json":
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    logger.warning("llm_json_parse_failed", provider="openai", content=content[:200])

            usage = response.usage
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0

            logger.info(
                "llm_analyze",
                provider="openai",
                model=self.model,
                task=context.task_type.value,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=round(latency, 1),
            )

            return StructuredOutput(
                content=content,
                parsed=parsed,
                model=self.model,
                provider="openai",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency,
            )
        except openai.APIError as e:
            logger.error("llm_analyze_error", provider="openai", error=str(e))
            raise

    async def health_check(self) -> ProviderStatus:
        start = time.monotonic()
        try:
            await self.client.chat.completions.create(
                model=self.model,
                max_tokens=5,
                messages=[{"role": "user", "content": "ping"}],
            )
            latency = (time.monotonic() - start) * 1000
            return ProviderStatus(
                provider="openai",
                status=ProviderHealthStatus.HEALTHY,
                last_check_at=datetime.now(UTC).isoformat(),
                average_latency_ms=latency,
            )
        except Exception:
            return ProviderStatus(
                provider="openai",
                status=ProviderHealthStatus.DOWN,
                last_check_at=datetime.now(UTC).isoformat(),
                consecutive_failures=1,
            )
