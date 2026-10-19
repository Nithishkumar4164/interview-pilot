"""ClaudeCodeProvider: the options each CLI run gets and how its result is read.

The SDK's ``query`` is faked; a real round trip through the signed-in CLI is in
test_live_models.py (opt-in).
"""

import json

import jsonschema
import pytest
from claude_agent_sdk import ResultMessage, StreamEvent

from src.llm.config import TaskProviderMapping
from src.llm.factory import ProviderFactory
from src.llm.failover import FailoverManager
from src.llm.health import HealthMonitor
from src.llm.interface import AnalysisContext, ChatMessage, LLMConfig, TaskType, ToolCall, ToolSpec
from src.llm.providers import claude_code
from src.llm.providers.claude_code import ClaudeCodeProvider, render_conversation, tool_turn_schema
from src.llm.tools import run_tool_loop

CAPITAL = ToolSpec(
    name="lookup_capital",
    description="Capital city of a country.",
    input_schema={"type": "object", "properties": {"country": {"type": "string"}}, "required": ["country"]},
)
TIME = ToolSpec(
    name="local_time",
    description="Current time in a city.",
    input_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
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


def _result(result: str | None = None, structured_output=None, stop_reason: str = "end_turn") -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=2,
        session_id="s",
        stop_reason=stop_reason,
        usage={
            "input_tokens": 3,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 20,
            "output_tokens": 7,
        },
        result=result,
        structured_output=structured_output,
    )


class FakeQuery:
    """Stands in for ``claude_agent_sdk.query``: records each run, replays scripted messages."""

    def __init__(self, *runs: list) -> None:
        self.runs = list(runs)
        self.calls: list[tuple[str, object]] = []

    async def __call__(self, *, prompt, options):
        self.calls.append((prompt, options))
        for message in self.runs.pop(0):
            yield message


@pytest.fixture
def fake_query(monkeypatch):
    def install(*runs: list) -> FakeQuery:
        fake = FakeQuery(*runs)
        monkeypatch.setattr(claude_code, "query", fake)
        return fake

    return install


def test_the_conversation_renders_calls_and_results_with_their_ids() -> None:
    assert render_conversation(CONVERSATION) == (
        "The conversation so far:\n\n"
        "<user>\nCapitals of France and Japan?\n</user>\n\n"
        "<assistant>\nLooking them up.\n"
        '<tool_call id="t1" name="lookup_capital">{"country": "France"}</tool_call>\n'
        '<tool_call id="t2" name="lookup_capital">{"country": "Japan"}</tool_call>\n</assistant>\n\n'
        '<tool_result id="t1">\nParis\n</tool_result>\n\n'
        '<tool_result id="t2">\nTokyo\n</tool_result>\n\n'
        "Write the assistant's next turn (only the next turn; tool results come back to you afterwards)."
    )


def test_the_reply_schema_only_admits_known_tools_with_valid_arguments() -> None:
    schema = tool_turn_schema([CAPITAL, TIME])
    jsonschema.validate(
        {
            "text": "",
            "tool_calls": [
                {"name": "lookup_capital", "arguments": {"country": "Peru"}},
                {"name": "local_time", "arguments": {"city": "Lima"}},
            ],
        },
        schema,
    )
    for bad in (
        {"name": "delete_everything", "arguments": {}},
        {"name": "lookup_capital", "arguments": {}},  # missing "country"
        {"name": "lookup_capital", "arguments": {"city": "Lima"}, "extra": 1},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"text": "", "tool_calls": [bad]}, schema)


async def test_a_tool_turn_runs_a_bare_cli_and_returns_the_requested_calls(fake_query) -> None:
    fake = fake_query(
        [
            _result(
                structured_output={
                    "text": "Checking.",
                    "tool_calls": [
                        {"name": "lookup_capital", "arguments": {"country": "Peru"}},
                        {"name": "local_time", "arguments": {"city": "Lima"}},
                    ],
                }
            )
        ]
    )
    turn = await ClaudeCodeProvider("claude-sonnet-5").invoke_with_tools(
        "Be brief.", CONVERSATION[:1], [CAPITAL, TIME], LLMConfig(thinking=False)
    )

    prompt, options = fake.calls[0]
    assert prompt == render_conversation(CONVERSATION[:1])
    assert options.model == "claude-sonnet-5"
    assert options.system_prompt.startswith("Be brief.\n\n# How to reply")
    assert "## lookup_capital\nCapital city of a country." in options.system_prompt
    assert options.output_format == {"type": "json_schema", "schema": tool_turn_schema([CAPITAL, TIME])}
    # Nothing of the developer's own Claude Code setup leaks into the run.
    assert options.tools == [] and options.mcp_servers == {} and options.strict_mcp_config
    assert options.setting_sources == []
    assert options.extra_args == {"no-session-persistence": None}
    assert options.thinking == {"type": "disabled"}

    assert turn.text == "Checking." and turn.stop_reason == "tool_use" and turn.provider == "claude_code"
    assert [(c.name, c.arguments) for c in turn.tool_calls] == [
        ("lookup_capital", {"country": "Peru"}),
        ("local_time", {"city": "Lima"}),
    ]
    assert len({c.id for c in turn.tool_calls}) == 2
    assert (turn.input_tokens, turn.output_tokens, turn.cache_read_tokens, turn.cache_write_tokens) == (123, 7, 100, 20)


async def test_tool_choice_none_asks_for_a_plain_text_answer(fake_query) -> None:
    fake = fake_query([_result(result="Paris and Tokyo.")])
    turn = await ClaudeCodeProvider().invoke_with_tools(
        "Be brief.", CONVERSATION, [CAPITAL], LLMConfig(), tool_choice="none"
    )
    prompt, options = fake.calls[0]
    assert options.output_format is None and options.system_prompt == "Be brief."
    assert options.thinking is None  # thinking left to the model unless turned off
    assert '<tool_result id="t2">\nTokyo\n</tool_result>' in prompt
    assert turn.text == "Paris and Tokyo." and turn.tool_calls == [] and turn.stop_reason == "end"


async def test_a_run_without_a_structured_reply_fails_the_turn(fake_query) -> None:
    fake_query([_result(result="I could not comply.")])
    with pytest.raises(ValueError, match="no structured tool turn"):
        await ClaudeCodeProvider().invoke_with_tools("sys", CONVERSATION[:1], [CAPITAL], LLMConfig())


async def test_analyze_parses_json_answers(fake_query) -> None:
    fake = fake_query([_result(result='```json\n{"ok": true}\n```', stop_reason="max_tokens")])
    out = await ClaudeCodeProvider().analyze(
        AnalysisContext(system_prompt="Reply in JSON.", user_content="Go.", task_type=TaskType.REAL_TIME_ANALYSIS),
        LLMConfig(response_format="json"),
    )
    prompt, options = fake.calls[0]
    assert prompt == "Go." and options.system_prompt == "Reply in JSON." and options.output_format is None
    assert out.parsed == {"ok": True} and out.provider == "claude_code" and out.truncated
    assert (out.input_tokens, out.output_tokens) == (123, 7)


async def test_complete_streams_only_top_level_text_deltas(fake_query) -> None:
    def delta(text: str, parent: str | None = None) -> StreamEvent:
        event = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}
        return StreamEvent(uuid="u", session_id="s", event=event, parent_tool_use_id=parent)

    fake = fake_query([delta("Hel"), delta("ignored", parent="toolu_1"), delta("lo"), _result(result="Hello")])
    chunks = [c async for c in ClaudeCodeProvider().complete("Say hello.", LLMConfig())]
    assert chunks == ["Hel", "lo"]
    assert fake.calls[0][1].include_partial_messages


async def test_a_tool_loop_runs_end_to_end_through_the_failover_manager(fake_query) -> None:
    """The path the graphs take: run_tool_loop -> FailoverManager -> ClaudeCodeProvider."""
    fake = fake_query(
        [
            _result(
                structured_output={
                    "text": "",
                    "tool_calls": [{"name": "lookup_capital", "arguments": {"country": "Peru"}}],
                }
            )
        ],
        [_result(structured_output={"text": "Lima", "tool_calls": []})],
    )
    ProviderFactory.register("claude_code", ClaudeCodeProvider)
    fm = FailoverManager(HealthMonitor())
    fm.update_mapping(
        TaskProviderMapping(
            task_type=TaskType.REPORT_CRITERION,
            primary_provider="claude_code",
            primary_model="claude-sonnet-5",
            fallback_provider="claude_code",
            fallback_model="claude-sonnet-5",
        )
    )

    async def execute(call: ToolCall) -> str:
        assert call.arguments == {"country": "Peru"}
        return "Lima"

    result = await run_tool_loop(
        fm, TaskType.REPORT_CRITERION, "sys", "Capital of Peru?", [CAPITAL], execute, LLMConfig()
    )
    assert result.text == "Lima" and result.stop_reason == "end"
    assert result.turns == 2 and result.tool_calls == 1 and result.providers == ["claude_code", "claude_code"]
    # The second run sees the first call and its result, matched by id.
    call_id = result.messages[1].tool_calls[0].id
    second_prompt = fake.calls[1][0]
    assert (
        f'<tool_call id="{call_id}" name="lookup_capital">{json.dumps({"country": "Peru"})}</tool_call>'
        in second_prompt
    )
    assert f'<tool_result id="{call_id}">\nLima\n</tool_result>' in second_prompt
