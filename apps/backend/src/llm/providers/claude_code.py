"""Claude through the local Claude Code CLI (``claude-agent-sdk``), for local development.

Calls run on whatever account ``claude`` is signed in to, so no API key is
needed. Each call starts a fresh, stateless CLI run: no built-in tools, no MCP
servers, no user or project settings, no saved session.

Tool turns use structured output instead of native tool use. Claude Code runs
native tool calls itself and continues the conversation, while
``invoke_with_tools`` must hand the calls back to the caller after one turn. So
the tools are described in the system prompt, the conversation so far is
rendered as text, and the reply is a JSON object (validated against a schema
the CLI enforces) holding the answer text and the calls to make.
"""

import json
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, StreamEvent, query

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
from src.llm.providers.anthropic import _strip_code_fences
from src.utils.logging import get_logger

logger = get_logger("llm.claude_code")

# Structured output costs the CLI two turns (its StructuredOutput tool call and
# the acknowledgement), more if the first reply fails the schema.
_MAX_TURNS = 4

_TOOL_PROTOCOL = """

# How to reply

Your reply is an object with two fields, `text` and `tool_calls`. This is only
the envelope: it replaces native tool calling and does not change the
instructions above about when to use tools or how to format your answer.

- To call tools, list the calls in `tool_calls`. Their results come back in the
  next turn, so keep `text` to a short note (or empty) and do not guess them.
- To give your final answer, leave `tool_calls` empty and put the answer in
  `text`, exactly in the format the instructions above ask for. If they ask for
  JSON, `text` is that JSON document as a string.

# Tools

{tools}"""


def _describe_tools(tools: list[ToolSpec]) -> str:
    return "\n\n".join(
        f"## {t.name}\n{t.description}\nArguments (JSON Schema): {json.dumps(t.input_schema)}" for t in tools
    )


def tool_turn_schema(tools: list[ToolSpec]) -> dict[str, Any]:
    """JSON Schema of a tool turn reply: each call must name a tool and match its arguments."""
    calls = [
        {
            "type": "object",
            "properties": {"name": {"const": t.name}, "arguments": t.input_schema},
            "required": ["name", "arguments"],
            "additionalProperties": False,
        }
        for t in tools
    ]
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "tool_calls": {"type": "array", "items": {"anyOf": calls}},
        },
        "required": ["text", "tool_calls"],
        "additionalProperties": False,
    }


def render_conversation(messages: list[ChatMessage]) -> str:
    """A provider-neutral tool conversation as a plain-text transcript."""
    parts: list[str] = []
    for m in messages:
        if m.role == "user":
            parts.append(f"<user>\n{m.content}\n</user>")
        elif m.role == "assistant":
            body = [m.content] if m.content else []
            body += [
                f'<tool_call id="{c.id}" name="{c.name}">{json.dumps(c.arguments)}</tool_call>' for c in m.tool_calls
            ]
            parts.append("<assistant>\n" + "\n".join(body) + "\n</assistant>")
        elif m.role == "tool":
            parts.append(f'<tool_result id="{m.tool_call_id}">\n{m.content}\n</tool_result>')
        else:
            raise ValueError(f"unknown message role {m.role!r}")
    return (
        "The conversation so far:\n\n"
        + "\n\n".join(parts)
        + "\n\nWrite the assistant's next turn (only the next turn; tool results come back to you afterwards)."
    )


def _usage(result: ResultMessage) -> tuple[int, int, int, int]:
    """(all input, output, cache read, cache write) tokens of a run."""
    usage = result.usage or {}
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    cache_write = usage.get("cache_creation_input_tokens", 0) or 0
    total_input = (usage.get("input_tokens", 0) or 0) + cache_read + cache_write
    return total_input, usage.get("output_tokens", 0) or 0, cache_read, cache_write


class ClaudeCodeProvider(ILLMProvider):
    """Claude via the signed-in Claude Code CLI (development only)."""

    def __init__(self, model: str = "claude-sonnet-5") -> None:
        self.model = model

    @property
    def provider_name(self) -> str:
        return "claude_code"

    def _options(self, system: str | None, config: LLMConfig, **kwargs: Any) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            model=self.model,
            system_prompt=system or "",
            tools=[],  # no Bash, Read, Edit...: the model only answers
            mcp_servers={},
            strict_mcp_config=True,
            setting_sources=[],  # ignore user/project CLAUDE.md, hooks and settings
            # Prompts carry candidate data: keep them out of ~/.claude/projects.
            extra_args={"no-session-persistence": None},
            # strict_mcp_config does not cover the claude.ai connectors; they add seconds to startup.
            env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false"},
            cwd=tempfile.gettempdir(),
            thinking=None if config.thinking else {"type": "disabled"},
            max_turns=_MAX_TURNS,
            **kwargs,
        )

    async def _run(self, prompt: str, options: ClaudeAgentOptions) -> ResultMessage:
        """Run the CLI to completion. A failed run raises ``claude_agent_sdk.ResultError``."""
        result: ResultMessage | None = None
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                result = message
        if result is None:
            raise RuntimeError("Claude Code ended without a result")
        return result

    async def complete(self, prompt: str, config: LLMConfig) -> AsyncIterator[str]:
        start = time.monotonic()
        options = self._options(None, config, include_partial_messages=True)
        try:
            async for message in query(prompt=prompt, options=options):
                if not isinstance(message, StreamEvent) or message.parent_tool_use_id:
                    continue
                event = message.event
                if event.get("type") == "content_block_delta" and event["delta"].get("type") == "text_delta":
                    yield event["delta"]["text"]
        except Exception as e:
            logger.error("llm_complete_error", provider="claude_code", error=str(e))
            raise
        logger.info(
            "llm_complete",
            provider="claude_code",
            model=self.model,
            latency_ms=round((time.monotonic() - start) * 1000, 1),
        )

    async def analyze(self, context: AnalysisContext, config: LLMConfig) -> StructuredOutput:
        start = time.monotonic()
        try:
            result = await self._run(context.user_content, self._options(context.system_prompt, config))
        except Exception as e:
            logger.error("llm_analyze_error", provider="claude_code", error=str(e))
            raise
        latency = (time.monotonic() - start) * 1000
        content = result.result or ""
        parsed = None
        if config.response_format == "json":
            try:
                parsed = json.loads(_strip_code_fences(content))
            except json.JSONDecodeError:
                logger.warning("llm_json_parse_failed", provider="claude_code", content=content[:200])
        input_tokens, output_tokens, _, _ = _usage(result)
        logger.info(
            "llm_analyze",
            provider="claude_code",
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
            provider="claude_code",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency,
            # The CLI takes no max_tokens, so an answer is cut only at the model's own limit.
            truncated=result.stop_reason == "max_tokens",
        )

    async def invoke_with_tools(
        self,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        config: LLMConfig,
        tool_choice: str = "auto",
    ) -> ToolTurn:
        start = time.monotonic()
        offer_tools = bool(tools) and tool_choice != "none"
        if offer_tools:
            options = self._options(
                system + _TOOL_PROTOCOL.format(tools=_describe_tools(tools)),
                config,
                output_format={"type": "json_schema", "schema": tool_turn_schema(tools)},
            )
        else:
            # tool_choice="none": earlier calls stay in the transcript, but the reply is plain text.
            options = self._options(system, config)
        try:
            result = await self._run(render_conversation(messages), options)
        except Exception as e:
            logger.error("llm_tools_error", provider="claude_code", error=str(e))
            raise
        latency = (time.monotonic() - start) * 1000

        if offer_tools:
            reply = result.structured_output
            if not isinstance(reply, dict):
                raise ValueError(f"Claude Code returned no structured tool turn: {result.result!r:.200}")
            text = reply.get("text", "")
            calls = [
                ToolCall(id=f"call_{uuid.uuid4().hex[:12]}", name=c["name"], arguments=dict(c.get("arguments") or {}))
                for c in reply.get("tool_calls", [])
            ]
        else:
            text, calls = result.result or "", []
        stop = "tool_use" if calls else ("max_tokens" if result.stop_reason == "max_tokens" else "end")

        input_tokens, output_tokens, cache_read, cache_write = _usage(result)
        logger.info(
            "llm_invoke_with_tools",
            provider="claude_code",
            model=self.model,
            tool_calls=len(calls),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=round(latency, 1),
        )
        return ToolTurn(
            text=text,
            tool_calls=calls,
            stop_reason=stop,
            model=self.model,
            provider="claude_code",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

    async def health_check(self) -> ProviderStatus:
        # The monitor pings every few seconds; a model call each time would burn the
        # account's usage limits, and the SDK ships its own CLI so there is nothing
        # to probe locally. Failed calls still open the circuit.
        return ProviderStatus(
            provider="claude_code",
            status=ProviderHealthStatus.HEALTHY,
            last_check_at=datetime.now(UTC).isoformat(),
        )
