"""Tool calling through the LLM layer: provider translation, failover, the loop.

Provider tests fake only the SDK client and check the exact request each API
receives and how its response is read. Live round trips against the real
APIs are in test_live_models.py (opt-in).
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.llm.config import TaskProviderMapping
from src.llm.interface import ChatMessage, LLMConfig, TaskType, ToolCall, ToolSpec
from src.llm.providers.anthropic import AnthropicProvider, to_anthropic_messages
from src.llm.providers.openai import OpenAIProvider, to_openai_messages
from src.llm.tools import run_tool_loop
from src.replay.runner import build_scripted_failover_manager

CAPITAL = ToolSpec(
    name="lookup_capital",
    description="Capital city of a country.",
    input_schema={"type": "object", "properties": {"country": {"type": "string"}}, "required": ["country"]},
)

CONVERSATION = [
    ChatMessage(role="user", content="Capitals of France and Japan?"),
    ChatMessage(
        role="assistant",
        content="Looking them up.",
        tool_calls=[
            ToolCall(id="t1", name="lookup_capital", arguments={"country": "France"}),
            ToolCall(id="t2", name="lookup_capital", arguments={"country": "Japan"}),
        ],
    ),
    ChatMessage(role="tool", content="Paris", tool_call_id="t1"),
    ChatMessage(role="tool", content="Tokyo", tool_call_id="t2"),
]


def test_anthropic_translation_groups_tool_results_into_one_user_turn() -> None:
    assert to_anthropic_messages(CONVERSATION) == [
        {"role": "user", "content": "Capitals of France and Japan?"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Looking them up."},
                {"type": "tool_use", "id": "t1", "name": "lookup_capital", "input": {"country": "France"}},
                {"type": "tool_use", "id": "t2", "name": "lookup_capital", "input": {"country": "Japan"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "Paris"},
                {"type": "tool_result", "tool_use_id": "t2", "content": "Tokyo"},
            ],
        },
    ]


def test_openai_translation_uses_tool_role_messages() -> None:
    assert to_openai_messages("sys", CONVERSATION) == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Capitals of France and Japan?"},
        {
            "role": "assistant",
            "content": "Looking them up.",
            "tool_calls": [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {"name": "lookup_capital", "arguments": '{"country": "France"}'},
                },
                {
                    "id": "t2",
                    "type": "function",
                    "function": {"name": "lookup_capital", "arguments": '{"country": "Japan"}'},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "Paris"},
        {"role": "tool", "tool_call_id": "t2", "content": "Tokyo"},
    ]


async def test_anthropic_provider_request_and_response() -> None:
    provider = AnthropicProvider("claude-sonnet-5")
    provider.client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    content=[
                        SimpleNamespace(type="text", text="One moment."),
                        SimpleNamespace(
                            type="tool_use", id="toolu_1", name="lookup_capital", input={"country": "Peru"}
                        ),
                    ],
                    stop_reason="tool_use",
                    usage=SimpleNamespace(input_tokens=50, output_tokens=12),
                )
            )
        )
    )
    turn = await provider.invoke_with_tools("sys", CONVERSATION[:1], [CAPITAL], LLMConfig(max_tokens=100), "auto")
    kwargs = provider.client.messages.create.await_args.kwargs
    assert kwargs["tools"] == [
        {
            "name": "lookup_capital",
            "description": "Capital city of a country.",
            "input_schema": CAPITAL.input_schema,
            "cache_control": {"type": "ephemeral"},  # system + tools repeat every turn: cached
        }
    ]
    assert kwargs["tool_choice"] == {"type": "auto"}
    assert kwargs["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert turn.text == "One moment." and turn.stop_reason == "tool_use"
    assert turn.tool_calls == [ToolCall(id="toolu_1", name="lookup_capital", arguments={"country": "Peru"})]
    assert (turn.input_tokens, turn.output_tokens, turn.provider) == (50, 12, "anthropic")


async def test_openai_provider_request_and_response() -> None:
    provider = OpenAIProvider("gpt-4o")
    message = SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(
                id="call_1",
                function=SimpleNamespace(name="lookup_capital", arguments=json.dumps({"country": "Peru"})),
            )
        ],
    )
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
                        usage=SimpleNamespace(prompt_tokens=40, completion_tokens=9),
                    )
                )
            )
        )
    )
    turn = await provider.invoke_with_tools("sys", CONVERSATION[:1], [CAPITAL], LLMConfig(max_tokens=100), "none")
    kwargs = provider.client.chat.completions.create.await_args.kwargs
    assert kwargs["tools"][0] == {
        "type": "function",
        "function": {
            "name": "lookup_capital",
            "description": "Capital city of a country.",
            "parameters": CAPITAL.input_schema,
        },
    }
    assert kwargs["tool_choice"] == "none"
    assert turn.text == "" and turn.stop_reason == "tool_use"
    assert turn.tool_calls == [ToolCall(id="call_1", name="lookup_capital", arguments={"country": "Peru"})]


class _Failing:
    provider_name = "broken"

    async def invoke_with_tools(self, *args, **kwargs):
        raise ConnectionError("primary down")


async def test_a_tool_turn_fails_over_to_the_fallback_provider() -> None:
    fm = build_scripted_failover_manager()
    fm.update_mapping(
        TaskProviderMapping(
            task_type=TaskType.REPORT_CRITERION,
            primary_provider="broken",
            primary_model="x",
            fallback_provider="scripted",
            fallback_model="scripted-v1",
        )
    )
    fm._provider_cache["broken:x"] = _Failing()  # type: ignore[assignment]
    fm.health_monitor.register_provider("broken", _Failing())  # type: ignore[arg-type]
    turn = await fm.invoke_with_tools(TaskType.REPORT_CRITERION, "sys", CONVERSATION[:1], [CAPITAL])
    assert turn.provider == "scripted"
    assert fm.get_failover_log()[-1]["reason"] == "primary_error: primary down"


async def test_the_loop_runs_tools_feeds_errors_back_and_stops() -> None:
    fm = build_scripted_failover_manager()
    executed: list[str] = []

    async def execute(call: ToolCall) -> str:
        executed.append(call.name)
        if call.name == "flaky":
            raise RuntimeError("lookup failed")
        return "Lima"

    flaky = ToolSpec(name="flaky", description="Always fails.", input_schema={"type": "object", "properties": {}})
    result = await run_tool_loop(
        fm, TaskType.REPORT_CRITERION, "sys", "Capital of Peru?", [CAPITAL, flaky], execute, LLMConfig()
    )
    assert executed == ["lookup_capital", "flaky"]
    tool_results = [m.content for m in result.messages if m.role == "tool"]
    assert tool_results == ["Lima", "error: lookup failed"]
    assert result.stop_reason == "end" and result.text and result.turns == 2 and result.tool_calls == 2


async def test_the_last_step_forces_an_answer() -> None:
    fm = build_scripted_failover_manager()
    choices: list[str] = []
    original = fm.invoke_with_tools

    async def spy(*args, tool_choice="auto", **kwargs):
        choices.append(tool_choice)
        return await original(*args, tool_choice=tool_choice, **kwargs)

    fm.invoke_with_tools = spy  # type: ignore[method-assign]

    async def execute(call: ToolCall) -> str:
        return "Lima"

    result = await run_tool_loop(
        fm, TaskType.REPORT_CRITERION, "sys", "Capital of Peru?", [CAPITAL], execute, LLMConfig(), max_steps=1
    )
    assert choices == ["none"] and result.stop_reason == "end" and result.tool_calls == 0


async def test_anthropic_drops_temperature_for_models_that_reject_it() -> None:
    import anthropic
    import httpx

    from src.llm.interface import AnalysisContext

    rejected = anthropic.BadRequestError(
        "`temperature` is deprecated for this model.",
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")),
        body=None,
    )
    ok = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"ok": true}')],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )
    provider = AnthropicProvider("claude-sonnet-5")
    create = AsyncMock(side_effect=[rejected, ok, ok])
    provider.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    context = AnalysisContext(system_prompt="s", user_content="u", task_type=TaskType.REAL_TIME_ANALYSIS)
    config = LLMConfig(max_tokens=10, temperature=0.3, response_format="json")

    assert (await provider.analyze(context, config)).parsed == {"ok": True}
    assert (await provider.analyze(context, config)).parsed == {"ok": True}
    sent = [call.kwargs for call in create.await_args_list]
    assert [("temperature" in k) for k in sent] == [True, False, False]  # retried once, then never sent


async def test_anthropic_retries_concurrent_temperature_rejections() -> None:
    """Parallel calls sent before the first rejection came back must each be retried too."""
    import asyncio

    import anthropic
    import httpx

    from src.llm.interface import AnalysisContext

    def rejected() -> anthropic.BadRequestError:
        return anthropic.BadRequestError(
            "`temperature` is deprecated for this model.",
            response=httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")),
            body=None,
        )

    ok = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='{"ok": true}')],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )
    gate = asyncio.Event()

    async def create(**kwargs):
        if "temperature" in kwargs:
            await gate.wait()  # both requests are in flight before either is rejected
            raise rejected()
        return ok

    provider = AnthropicProvider("claude-sonnet-5")
    provider.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    context = AnalysisContext(system_prompt="s", user_content="u", task_type=TaskType.REAL_TIME_ANALYSIS)
    config = LLMConfig(max_tokens=10, temperature=0.3, response_format="json")
    calls = [asyncio.create_task(provider.analyze(context, config)) for _ in range(3)]
    await asyncio.sleep(0)
    gate.set()
    assert [r.parsed for r in await asyncio.gather(*calls)] == [{"ok": True}] * 3


async def test_anthropic_analyze_skips_thinking_blocks() -> None:
    """Current models can return a thinking block before the text."""
    from src.llm.interface import AnalysisContext

    provider = AnthropicProvider("claude-opus-5-5")
    provider.client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(
                    content=[
                        SimpleNamespace(type="thinking", thinking="..."),
                        SimpleNamespace(type="text", text='{"recommendation": "hire"}'),
                    ],
                    stop_reason="end_turn",
                    usage=SimpleNamespace(input_tokens=5, output_tokens=3),
                )
            )
        )
    )
    context = AnalysisContext(system_prompt="s", user_content="u", task_type=TaskType.REPORT_GENERATION)
    result = await provider.analyze(context, LLMConfig(max_tokens=10, temperature=0.3, response_format="json"))
    assert result.parsed == {"recommendation": "hire"}


async def test_a_loop_still_exploring_at_the_last_step_is_told_to_answer() -> None:
    """Real models answered in prose when only tool_choice=none stopped them."""
    from src.llm.interface import ToolTurn

    seen: list[list[ChatMessage]] = []

    class Explorer:
        async def invoke_with_tools(
            self, task_type, system, messages, tools, config, session_id=None, tool_choice="auto"
        ):
            seen.append(list(messages))
            if tool_choice == "none":
                return ToolTurn(text='{"done": true}', tool_calls=[], stop_reason="end")
            return ToolTurn(
                text="",
                tool_calls=[ToolCall(id=f"c{len(seen)}", name="lookup_capital", arguments={"country": "Peru"})],
                stop_reason="tool_use",
            )

    async def execute(call: ToolCall) -> str:
        return "Lima"

    result = await run_tool_loop(
        Explorer(),
        TaskType.REPORT_CRITERION,
        "sys",
        "q",
        [CAPITAL],
        execute,
        LLMConfig(),
        max_steps=3,
        final_instruction="Answer now in JSON.",
    )
    assert result.text == '{"done": true}'
    last_request = seen[-1]
    assert last_request[-1].role == "user" and last_request[-1].content == "Answer now in JSON."
    assert all(m.content != "Answer now in JSON." for m in seen[0] + seen[1])


def test_anthropic_translation_keeps_turns_alternating_after_tool_results() -> None:
    messages = [*CONVERSATION, ChatMessage(role="user", content="Answer now in JSON.")]
    out = to_anthropic_messages(messages)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[-1]["content"][-1] == {"type": "text", "text": "Answer now in JSON."}
