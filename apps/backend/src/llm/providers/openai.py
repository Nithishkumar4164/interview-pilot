import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import openai

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

logger = get_logger("llm.openai")


def _strip_code_fences(text: str) -> str:
    """Strip markdown code fences (```json ... ```) from LLM output."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.index("\n")
        stripped = stripped[first_newline + 1 :]
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    return stripped.strip()


def to_openai_messages(system: str, messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Neutral conversation -> Chat Completions messages (system first)."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in m.tool_calls
                ]
            out.append(msg)
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        else:
            raise ValueError(f"unknown message role {m.role!r}")
    return out


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
                    parsed = json.loads(_strip_code_fences(content))
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
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                }
                for t in tools
            ]
            kwargs["tool_choice"] = tool_choice
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                messages=cast(Any, to_openai_messages(system, messages)),
                **kwargs,
            )
        except openai.APIError as e:
            logger.error("llm_tools_error", provider="openai", error=str(e))
            raise
        latency = (time.monotonic() - start) * 1000
        choice = response.choices[0]
        calls = []
        for c in choice.message.tool_calls or []:
            fn = getattr(c, "function", None)
            if fn is None:
                continue
            try:
                arguments = json.loads(fn.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_unparsed": fn.arguments}
            calls.append(ToolCall(id=c.id, name=fn.name, arguments=arguments if isinstance(arguments, dict) else {}))
        stop = {"tool_calls": "tool_use", "length": "max_tokens"}.get(choice.finish_reason or "", "end")
        usage = response.usage
        return ToolTurn(
            text=choice.message.content or "",
            tool_calls=calls,
            stop_reason=stop,
            model=self.model,
            provider="openai",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency,
        )

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
