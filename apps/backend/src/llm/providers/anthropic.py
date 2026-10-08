import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import anthropic

from src.config import settings
from src.llm.interface import (
    AnalysisContext,
    ChatMessage,
    ILLMProvider,
    LLMConfig,
    ProviderHealthStatus,
    ProviderStatus,
    StructuredOutput,
    ToolCall,
    ToolSpec,
    ToolTurn,
)
from src.utils.logging import get_logger

logger = get_logger("llm.anthropic")


def _strip_code_fences(text: str) -> str:
    """Strip markdown code fences (```json ... ```) from LLM output."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = stripped.index("\n")
        stripped = stripped[first_newline + 1 :]
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    return stripped.strip()


def to_anthropic_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Neutral conversation -> Messages API: tool results become user tool_result blocks."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                # A user turn right after tool results joins them (turns must alternate).
                out[-1]["content"].append({"type": "text", "text": m.content})
            else:
                out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = [{"type": "text", "text": m.content}] if m.content else []
            blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.tool_calls]
            out.append({"role": "assistant", "content": blocks})
        elif m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            # Results of one assistant turn go back together in a single user message.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        else:
            raise ValueError(f"unknown message role {m.role!r}")
    return out


class AnthropicProvider(ILLMProvider):
    """Claude implementation of ILLMProvider."""

    def __init__(self, model: str = "claude-sonnet-5") -> None:
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._accepts_temperature = True

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _sampling(self, config: LLMConfig) -> dict[str, Any]:
        return {"temperature": config.temperature} if self._accepts_temperature else {}

    async def _create(self, config: LLMConfig, **kwargs: Any) -> Any:
        """messages.create, sending ``temperature`` only to models that accept it.

        Current models reject it with a 400 ("`temperature` is deprecated for
        this model"); on that error the call is retried once without it, and
        this provider stops sending it.
        """
        sampling = self._sampling(config)
        try:
            return await self.client.messages.create(**kwargs, **sampling)
        except anthropic.BadRequestError as e:
            # Decide from what *this* request sent: concurrent calls may have
            # flipped the flag while it was in flight.
            if not ("temperature" in sampling and "temperature" in str(e)):
                raise
            if self._accepts_temperature:
                logger.info("anthropic_temperature_unsupported", model=self.model)
                self._accepts_temperature = False
            return await self.client.messages.create(**kwargs)

    async def complete(self, prompt: str, config: LLMConfig) -> AsyncIterator[str]:
        start = time.monotonic()
        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=config.max_tokens,
                **self._sampling(config),
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
            response = await self._create(
                config,
                model=self.model,
                max_tokens=config.max_tokens,
                system=context.system_prompt,
                messages=[{"role": "user", "content": context.user_content}],
            )
            latency = (time.monotonic() - start) * 1000
            # Current models may put thinking blocks before the answer; keep text blocks only.
            content = "".join(b.text for b in response.content if b.type == "text")
            if response.stop_reason == "max_tokens":
                logger.warning(
                    "llm_output_truncated", provider="anthropic", model=self.model, max_tokens=config.max_tokens
                )

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
                truncated=response.stop_reason == "max_tokens",
            )
        except anthropic.APIError as e:
            logger.error("llm_analyze_error", provider="anthropic", error=str(e))
            raise

    async def invoke_with_tools(
        self,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        config: LLMConfig,
        tool_choice: str = "auto",
    ) -> ToolTurn:
        start = time.monotonic()
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
            ]
            # Every turn of a loop re-sends the same system prompt and tools: cache them.
            kwargs["tools"][-1]["cache_control"] = {"type": "ephemeral"}
            kwargs["tool_choice"] = {"type": tool_choice}
        try:
            response = await self._create(
                config,
                model=self.model,
                max_tokens=config.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=cast(Any, to_anthropic_messages(messages)),
                **kwargs,
            )
        except anthropic.APIError as e:
            logger.error("llm_tools_error", provider="anthropic", error=str(e))
            raise
        latency = (time.monotonic() - start) * 1000
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input or {}))
            for b in response.content
            if b.type == "tool_use"
        ]
        stop = {"tool_use": "tool_use", "max_tokens": "max_tokens"}.get(response.stop_reason or "", "end")
        if stop == "max_tokens":
            logger.warning("llm_output_truncated", provider="anthropic", model=self.model, max_tokens=config.max_tokens)
        logger.info(
            "llm_invoke_with_tools",
            provider="anthropic",
            model=self.model,
            tool_calls=len(calls),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=round(latency, 1),
        )
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        return ToolTurn(
            text=text,
            tool_calls=calls,
            stop_reason=stop,
            model=self.model,
            provider="anthropic",
            # The API reports uncached input separately from cache reads/writes.
            input_tokens=usage.input_tokens + cache_read + cache_write,
            output_tokens=usage.output_tokens,
            latency_ms=latency,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

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
        except Exception:
            return ProviderStatus(
                provider="anthropic",
                status=ProviderHealthStatus.DOWN,
                last_check_at=datetime.now(UTC).isoformat(),
                consecutive_failures=1,
            )
