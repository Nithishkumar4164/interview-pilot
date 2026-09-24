"""Spans emitted by FailoverManager.analyze."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from src.llm.config import TaskProviderMapping
from src.llm.factory import ProviderFactory
from src.llm.failover import FailoverManager
from src.llm.health import HealthMonitor
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.replay.scripted_provider import ScriptedProvider

_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)  # process-global; only this module installs one

SECRET = "candidate said: my SSN is 123-45-6789"


@pytest.fixture(autouse=True)
def _clear_spans():
    _EXPORTER.clear()
    yield


def _fm(primary: str, fallback: str) -> FailoverManager:
    ProviderFactory.register("scripted", ScriptedProvider)
    fm = FailoverManager(HealthMonitor())
    fm.update_mapping(TaskProviderMapping(TaskType.CODE_RATING, primary, f"{primary}-v1", fallback, f"{fallback}-v1"))
    return fm


def _ctx() -> AnalysisContext:
    return AnalysisContext(system_prompt="rate code", user_content=SECRET, task_type=TaskType.CODE_RATING)


def _only_span():
    spans = [s for s in _EXPORTER.get_finished_spans() if s.name == "llm.analyze"]
    assert len(spans) == 1
    return spans[0]


def _assert_no_prompt_text(span) -> None:
    blobs = [str(v) for v in span.attributes.values()]
    blobs += [str(v) for e in span.events for v in (e.attributes or {}).values()]
    assert not any("123-45-6789" in b or "candidate said" in b for b in blobs)


async def test_success_span_carries_call_metadata() -> None:
    await _fm("scripted", "scripted").analyze(_ctx(), LLMConfig(max_tokens=77), session_id="sess-1")

    span = _only_span()
    attrs = span.attributes
    assert attrs["llm.task_type"] == "code_rating"
    assert attrs["llm.provider"] == "scripted"
    assert attrs["llm.model"] == "scripted-v1"
    assert attrs["llm.max_tokens"] == 77
    assert attrs["session.id"] == "sess-1"
    assert attrs["llm.failover"] is False
    assert attrs["llm.input_tokens"] > 0
    assert span.status.status_code != StatusCode.ERROR
    _assert_no_prompt_text(span)


async def test_failover_is_recorded_on_the_span() -> None:
    fm = _fm("mock", "scripted")
    fm._get_provider("mock", "mock-v1").set_healthy(False)

    result = await fm.analyze(_ctx())

    assert result.provider == "scripted"
    span = _only_span()
    assert span.attributes["llm.failover"] is True
    assert span.attributes["llm.provider"] == "scripted"
    (event,) = [e for e in span.events if e.name == "llm.failover"]
    assert event.attributes == {"from_provider": "mock", "to_provider": "scripted", "reason": "ConnectionError"}
    _assert_no_prompt_text(span)


async def test_total_failure_marks_span_as_error() -> None:
    fm = _fm("mock", "mock")
    fm._get_provider("mock", "mock-v1").set_healthy(False)

    with pytest.raises(ConnectionError):
        await fm.analyze(_ctx())

    span = _only_span()
    assert span.status.status_code == StatusCode.ERROR
    assert [e.name for e in span.events].count("exception") == 1
    _assert_no_prompt_text(span)
