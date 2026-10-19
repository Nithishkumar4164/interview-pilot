"""Every model in the task -> provider map exists (real API calls; opt-in).

    LIVE_LLM=1 uv run pytest tests/test_live_models.py

Each call sends only "Reply with the single word OK." with a 5-token cap.
The map pointed at retired Claude 4 IDs that the API answered with 404, so
every call failed over (or failed outright where no OpenAI key was set).
"""

import os

import pytest

from src.config import settings
from src.llm.config import DEFAULT_TASK_MAPPINGS

pytestmark = pytest.mark.skipif(os.environ.get("LIVE_LLM") != "1", reason="real API calls; set LIVE_LLM=1")

PROMPT = "Reply with the single word OK."


def _configured(provider: str) -> list[str]:
    models = set()
    for m in DEFAULT_TASK_MAPPINGS.values():
        if m.primary_provider == provider:
            models.add(m.primary_model)
        if m.fallback_provider == provider:
            models.add(m.fallback_model)
    return sorted(models)


@pytest.mark.parametrize("model", _configured("anthropic"))
async def test_anthropic_model_exists(model: str) -> None:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(model=model, max_tokens=5, messages=[{"role": "user", "content": PROMPT}])
    assert response.content


@pytest.mark.parametrize("model", _configured("openai"))
async def test_openai_model_exists(model: str) -> None:
    if settings.openai_api_key in ("", "test-key"):  # conftest's placeholder
        pytest.skip("no OpenAI key configured")
    import openai

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=model, max_tokens=5, messages=[{"role": "user", "content": PROMPT}]
    )
    assert response.choices


async def _capital_loop(fm, task_type):
    from src.llm.interface import LLMConfig, ToolCall, ToolSpec
    from src.llm.tools import run_tool_loop

    calls: list[dict] = []

    async def execute(call: ToolCall) -> str:
        calls.append(call.arguments)
        return {"peru": "Lima"}.get(str(call.arguments.get("country", "")).lower(), "unknown")

    tool = ToolSpec(
        name="lookup_capital",
        description="Look up the capital city of a country. Always use this; do not answer from memory.",
        input_schema={"type": "object", "properties": {"country": {"type": "string"}}, "required": ["country"]},
    )
    result = await run_tool_loop(
        fm,
        task_type,
        "Answer using the tool. Reply with just the city name.",
        "What is the capital of Peru?",
        [tool],
        execute,
        LLMConfig(max_tokens=100, temperature=0),
        max_steps=3,
    )
    return result, calls


async def test_anthropic_tool_loop_round_trip() -> None:
    """Real tool use: the model calls the tool, gets the result, answers with it."""
    from src.llm.factory import ProviderFactory  # noqa: F401  (providers registered)
    from src.llm.failover import FailoverManager
    from src.llm.health import HealthMonitor
    from src.llm.interface import TaskType

    result, calls = await _capital_loop(FailoverManager(HealthMonitor()), TaskType.REPORT_CRITERION)
    assert [c.get("country", "").lower() for c in calls] == ["peru"]
    assert "lima" in result.text.lower() and result.stop_reason == "end"
    assert result.providers[0] == "anthropic"


async def test_anthropic_tool_loop_fails_over_mid_conversation() -> None:
    """Primary model ID does not exist (404); every turn runs on the fallback."""
    from src.llm.config import TaskProviderMapping
    from src.llm.failover import FailoverManager
    from src.llm.health import HealthMonitor
    from src.llm.interface import TaskType

    fm = FailoverManager(HealthMonitor())
    fm.update_mapping(
        TaskProviderMapping(
            task_type=TaskType.REPORT_CRITERION,
            primary_provider="anthropic",
            primary_model="claude-does-not-exist",
            fallback_provider="anthropic",
            fallback_model="claude-sonnet-5",
        )
    )
    # Distinct provider-cache entries, one health record: the 404s open the circuit,
    # after which turns go straight to the fallback.
    result, calls = await _capital_loop(fm, TaskType.REPORT_CRITERION)
    assert "lima" in result.text.lower()
    assert any(entry["reason"].startswith("primary_error") for entry in fm.get_failover_log())


async def test_analyze_succeeds_on_the_primary_with_production_settings() -> None:
    """The real-time analysis path exactly as configured (model, temperature, JSON), synthetic content.

    The current Claude models reject `temperature` with a 400; every call then
    failed over (or failed). The primary must answer.
    """
    from src.llm.failover import FailoverManager
    from src.llm.health import HealthMonitor
    from src.llm.interface import AnalysisContext, LLMConfig, TaskType

    fm = FailoverManager(HealthMonitor())
    result = await fm.analyze(
        AnalysisContext(
            system_prompt='Reply with JSON {"ok": true} and nothing else.',
            user_content="Synthetic test message.",
            task_type=TaskType.REAL_TIME_ANALYSIS,
        ),
        LLMConfig(max_tokens=20, temperature=0.3, response_format="json"),
    )
    assert result.provider == "anthropic" and result.parsed == {"ok": True}
    assert not fm.get_failover_log()
