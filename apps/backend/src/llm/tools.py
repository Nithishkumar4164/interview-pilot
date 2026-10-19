"""Tool-use loops over FailoverManager.invoke_with_tools.

A loop runs model turns until the model stops asking for tools (or a step
cap): each requested call is executed by ``execute`` and its result fed back.
Tools here are read-only lookups the graph exposes to the model; a tool that
raises returns an error string to the model instead of failing the loop.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from src.llm.interface import ChatMessage, LLMConfig, TaskType, ToolCall, ToolSpec, ToolTurn
from src.utils.logging import get_logger

logger = get_logger("llm.tools")

ToolExecutor = Callable[[ToolCall], Awaitable[str]]


@dataclass
class ToolLoopResult:
    text: str  # the model's final answer
    stop_reason: str  # "end" | "max_tokens" | "max_steps"
    messages: list[ChatMessage]  # the whole conversation, final answer included
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    providers: list[str] = field(default_factory=list)


async def run_tool_loop(
    failover,  # FailoverManager (not imported: failover imports this module's types)
    task_type: TaskType,
    system: str,
    user: str,
    tools: list[ToolSpec],
    execute: ToolExecutor,
    config: LLMConfig,
    session_id: str | None = None,
    max_steps: int = 6,
    final_instruction: str = "Stop using tools. Reply now with your final answer, in the format you were asked for.",
) -> ToolLoopResult:
    """Run a tool conversation to completion. LLM errors propagate (the caller decides on retries).

    On the last allowed step tools are switched off (``tool_choice="none"``)
    and, if the model was still exploring, ``final_instruction`` is added as a
    user turn: without it the model tends to answer in prose ("I would next
    look at...") instead of the requested format.
    """
    messages = [ChatMessage(role="user", content=user)]
    result = ToolLoopResult(text="", stop_reason="max_steps", messages=messages)
    for step in range(max_steps):
        # On the last allowed step the model must answer instead of calling tools.
        choice = "auto" if step < max_steps - 1 else "none"
        if choice == "none" and messages[-1].role == "tool":
            messages.append(ChatMessage(role="user", content=final_instruction))
        turn: ToolTurn = await failover.invoke_with_tools(
            task_type, system, messages, tools, config, session_id=session_id, tool_choice=choice
        )
        result.turns += 1
        result.input_tokens += turn.input_tokens
        result.output_tokens += turn.output_tokens
        result.providers.append(turn.provider)
        messages.append(ChatMessage(role="assistant", content=turn.text, tool_calls=list(turn.tool_calls)))
        if not turn.tool_calls:
            result.text = turn.text
            result.stop_reason = turn.stop_reason
            return result
        for call in turn.tool_calls:
            result.tool_calls += 1
            try:
                output = await execute(call)
            except Exception as e:
                logger.warning("tool_failed", tool=call.name, error=str(e))
                output = f"error: {e}"
            messages.append(ChatMessage(role="tool", content=output, tool_call_id=call.id))
    return result
