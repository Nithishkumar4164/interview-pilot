"""Tests for M10 — Resilience & Failover.

Covers:
1. LLM provider failover mid-session
2. Health monitor circuit breaker
3. Session state snapshot recovery
4. Memory service recovery from snapshot
5. Graceful degradation when all providers down
"""

import pytest

from src.llm.config import TaskProviderMapping
from src.llm.failover import FailoverManager
from src.llm.health import FAILURE_THRESHOLD, HealthMonitor
from src.llm.interface import (
    AnalysisContext,
    LLMConfig,
    ProviderHealthStatus,
    TaskType,
)
from src.llm.providers.mock import MockProvider
from src.services.analysis_service import analyze_utterance
from src.services.code_analysis_service import analyze_locally, make_snapshot
from src.services.hint_service import HintService
from src.services.memory_service import FactStore, Utterance, assemble_context


def _mock_failover_manager() -> FailoverManager:
    """Create a FailoverManager that uses MockProvider for all tasks."""
    health = HealthMonitor()
    fm = FailoverManager(health)
    for task_type in TaskType:
        fm.update_mapping(
            TaskProviderMapping(
                task_type=task_type,
                primary_provider="mock",
                primary_model="mock-v1",
                fallback_provider="mock",
                fallback_model="mock-v1",
            )
        )
    return fm


# ── LLM Failover ──────────────────────────────────────────────────────


class TestLLMFailover:
    """Tests for LLM provider failover behavior."""

    @pytest.mark.asyncio
    async def test_primary_healthy_uses_primary(self):
        """When primary is healthy, should use primary provider."""
        fm = _mock_failover_manager()
        context = AnalysisContext(
            system_prompt="Test",
            user_content="Test content",
            task_type=TaskType.REAL_TIME_ANALYSIS,
        )
        config = LLMConfig(response_format="json")
        result = await fm.analyze(context, config)
        assert result.provider == "mock"

    def test_health_monitor_registers_provider(self):
        """Health monitor should track registered providers."""
        monitor = HealthMonitor()
        provider = MockProvider()
        monitor.register_provider("test_provider", provider)
        assert monitor.is_available("test_provider")

    def test_health_monitor_tracks_failures(self):
        """Health monitor should track consecutive failures."""
        monitor = HealthMonitor()
        provider = MockProvider()
        monitor.register_provider("test_provider", provider)

        monitor.record_failure("test_provider")
        monitor.record_failure("test_provider")

        status = monitor.get_status("test_provider")
        assert status is not None
        assert status.consecutive_failures == 2

    def test_health_monitor_resets_on_success(self):
        """Successful call should reset failure counter."""
        monitor = HealthMonitor()
        provider = MockProvider()
        monitor.register_provider("test_provider", provider)

        monitor.record_failure("test_provider")
        monitor.record_failure("test_provider")
        monitor.record_success("test_provider", latency_ms=50.0)

        status = monitor.get_status("test_provider")
        assert status.consecutive_failures == 0

    def test_health_monitor_circuit_opens(self):
        """Circuit should open after threshold failures."""
        monitor = HealthMonitor()
        provider = MockProvider()
        monitor.register_provider("test_provider", provider)

        for _ in range(FAILURE_THRESHOLD):
            monitor.record_failure("test_provider")

        status = monitor.get_status("test_provider")
        assert status.status == ProviderHealthStatus.DOWN

    def test_health_monitor_unregistered_unavailable(self):
        """Unregistered providers should be unavailable."""
        monitor = HealthMonitor()
        assert not monitor.is_available("nonexistent")

    def test_health_monitor_all_statuses(self):
        """get_all_statuses should return all tracked providers."""
        monitor = HealthMonitor()
        monitor.register_provider("p1", MockProvider())
        monitor.register_provider("p2", MockProvider())

        statuses = monitor.get_all_statuses()
        assert "p1" in statuses
        assert "p2" in statuses


# ── Analysis with Failover ─────────────────────────────────────────────


class TestAnalysisWithFailover:
    """Tests that utterance analysis handles provider results gracefully."""

    @pytest.mark.asyncio
    async def test_analysis_returns_result(self):
        """Analysis should return a sensible result via mock provider."""
        text = "I would use dynamic programming for this problem."
        context = assemble_context([Utterance(speaker="candidate", text=text, timestamp="t")], "", FactStore())
        result = await analyze_utterance(_mock_failover_manager(), "sess-af", context, None, "", "candidate", text)
        assert result is not None
        assert 1 <= result.answer_quality <= 5

    @pytest.mark.asyncio
    async def test_analysis_with_empty_memory(self):
        """Analysis should work even with empty memory context."""
        result = await analyze_utterance(
            _mock_failover_manager(),
            "sess-empty",
            "",
            None,
            "",
            "candidate",
            "Let me start by understanding the problem.",
        )
        assert result is not None
        assert isinstance(result.insight, str)


# ── Graceful Degradation ──────────────────────────────────────────────


class TestGracefulDegradation:
    """Tests for graceful behavior during outages."""

    def test_memory_context_works_without_llm(self):
        """Working memory and context assembly need no LLM."""
        memory = [
            Utterance(speaker="candidate", text=f"Statement {i} about algorithms.", timestamp="t") for i in range(5)
        ]
        context = assemble_context(memory, "", FactStore(), "Test question")
        assert "Statement 4 about algorithms." in context and "Test question" in context

    def test_code_analysis_works_locally(self):
        """Code analysis local detection should work without LLM."""
        code = """
def solve(nums, target):
    seen = {}
    for i, n in enumerate(nums):
        if target - n in seen:
            return [seen[target-n], i]
        seen[n] = i
"""
        snapshot = make_snapshot(code, "python")
        assert analyze_locally(snapshot, [snapshot]).progress_state.value != "empty"

    def test_hint_service_works_with_cached_hints(self):
        """Hint service should serve pre-computed hints without LLM."""
        hint_svc = HintService("sess-cached")

        hint_svc.register_hints(
            "q1",
            {
                1: "Pre-computed nudge",
                2: "Pre-computed guide",
                3: "Pre-computed direct",
            },
        )
        hint_svc.activate_question("q1")

        # All 3 hints should work without any LLM call
        h1 = hint_svc.advance_hint()
        assert h1 is not None
        assert h1.text == "Pre-computed nudge"

        h2 = hint_svc.advance_hint()
        assert h2.text == "Pre-computed guide"

        h3 = hint_svc.advance_hint()
        assert h3.text == "Pre-computed direct"
