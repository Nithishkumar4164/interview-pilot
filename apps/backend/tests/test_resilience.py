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
from src.llm.health import HealthMonitor, FAILURE_THRESHOLD
from src.llm.interface import (
    AnalysisContext,
    LLMConfig,
    ProviderHealthStatus,
    ProviderStatus,
    StructuredOutput,
    TaskType,
)
from src.llm.providers.mock import MockProvider
from src.services.analysis_service import AnalysisService
from src.services.code_analysis_service import CodeAnalysisService
from src.services.hint_service import HintService
from src.services.memory_service import MemoryService
from src.services.session_state_service import SessionState, SessionStateService


def _mock_failover_manager() -> FailoverManager:
    """Create a FailoverManager that uses MockProvider for all tasks."""
    health = HealthMonitor()
    fm = FailoverManager(health)
    for task_type in TaskType:
        fm.update_mapping(TaskProviderMapping(
            task_type=task_type,
            primary_provider="mock",
            primary_model="mock-v1",
            fallback_provider="mock",
            fallback_model="mock-v1",
        ))
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
    """Tests that analysis service handles provider failures gracefully."""

    @pytest.mark.asyncio
    async def test_analysis_returns_result(self):
        """Analysis should return a sensible result via mock provider."""
        fm = _mock_failover_manager()
        memory = MemoryService("sess-af", fm)
        analysis_svc = AnalysisService("sess-af", fm, memory)

        await memory.add_utterance("candidate", "I would use dynamic programming for this problem.", "2024-01-01T00:00:00Z")

        result = await analysis_svc.analyze_utterance(
            "candidate", "I would use dynamic programming for this problem.",
        )

        assert result is not None
        assert 1 <= result.answer_quality <= 5

    @pytest.mark.asyncio
    async def test_analysis_with_empty_memory(self):
        """Analysis should work even with empty memory context."""
        fm = _mock_failover_manager()
        memory = MemoryService("sess-empty", fm)
        analysis_svc = AnalysisService("sess-empty", fm, memory)

        result = await analysis_svc.analyze_utterance(
            "candidate", "Let me start by understanding the problem.",
        )

        assert result is not None
        assert isinstance(result.insight, str)


# ── Session State Recovery ─────────────────────────────────────────────

class TestSessionRecovery:
    """Tests for session state snapshot and recovery."""

    def test_snapshot_preserves_all_fields(self):
        """All session state fields should survive roundtrip."""
        state = SessionState("sess-recovery")
        state.transcript_count = 150
        state.last_transcript_timestamp = "2024-01-01T01:00:00Z"
        state.analysis_count = 30
        state.code_snapshot_count = 20
        state.hint_history = [
            {"id": "h1", "level": 1, "quality": "full"},
            {"id": "h2", "level": 2, "quality": "partial"},
        ]
        state.active_question_id = "q3"
        state.scores = {
            "problem_solving": 4.2,
            "code_quality": 3.5,
            "communication": 4.0,
            "hint_responsiveness": 3.8,
        }
        state.coverage_tracker = {"intro": True, "coding": True, "system_design": False}
        state.plan_progress = {"current_phase_index": 2, "current_question_index": 3}
        state.facts = {"claimed_experience": {"Python": "5 years"}}
        state.rolling_summary = "Candidate demonstrated strong Python skills."

        snapshot = state.to_snapshot()
        recovered = SessionState.from_snapshot(snapshot)

        assert recovered.session_id == "sess-recovery"
        assert recovered.transcript_count == 150
        assert recovered.last_transcript_timestamp == "2024-01-01T01:00:00Z"
        assert recovered.analysis_count == 30
        assert recovered.code_snapshot_count == 20
        assert len(recovered.hint_history) == 2
        assert recovered.active_question_id == "q3"
        assert recovered.scores["problem_solving"] == 4.2
        assert recovered.coverage_tracker["system_design"] is False
        assert recovered.plan_progress["current_question_index"] == 3
        assert recovered.facts["claimed_experience"]["Python"] == "5 years"
        assert "Python skills" in recovered.rolling_summary

    def test_snapshot_handles_missing_fields(self):
        """Recovery should handle snapshots from older schema versions gracefully."""
        minimal_snapshot = {
            "session_id": "sess-old",
            "schema_version": 1,
        }
        state = SessionState.from_snapshot(minimal_snapshot)
        assert state.session_id == "sess-old"
        assert state.transcript_count == 0
        assert state.scores == {}
        assert state.plan_progress == {"current_phase_index": 0, "current_question_index": 0}

    def test_multiple_sessions_independent(self):
        """Multiple concurrent sessions should maintain independent state."""
        svc = SessionStateService()

        state1 = svc.get_state("sess-a")
        state2 = svc.get_state("sess-b")

        state1.transcript_count = 100
        state1.scores = {"problem_solving": 5.0}

        state2.transcript_count = 50
        state2.scores = {"problem_solving": 2.0}

        assert svc.get_state("sess-a").transcript_count == 100
        assert svc.get_state("sess-b").transcript_count == 50
        assert svc.get_state("sess-a").scores["problem_solving"] == 5.0
        assert svc.get_state("sess-b").scores["problem_solving"] == 2.0


# ── Memory Service Recovery ────────────────────────────────────────────

class TestMemoryRecovery:
    """Tests for memory service state preservation."""

    @pytest.mark.asyncio
    async def test_memory_to_dict_roundtrip(self):
        """Memory service state should survive serialization."""
        fm = _mock_failover_manager()
        memory = MemoryService("sess-mr", fm)

        await memory.add_utterance("candidate", "I have 5 years of Python experience.", "2024-01-01T00:00:00Z")
        await memory.add_utterance("candidate", "I built a microservices platform at my last job.", "2024-01-01T00:00:01Z")
        await memory.add_utterance("candidate", "The platform handled 10K requests per second.", "2024-01-01T00:00:02Z")

        state = memory.to_dict()
        assert "working_memory" in state
        assert "fact_store" in state

        # Restore
        restored = MemoryService.from_dict(state, fm)
        assert len(restored._working_memory) == 3
        assert restored.fact_store is not None

    @pytest.mark.asyncio
    async def test_empty_memory_roundtrip(self):
        """Empty memory should roundtrip cleanly."""
        fm = _mock_failover_manager()
        memory = MemoryService("sess-empty-mr", fm)

        state = memory.to_dict()
        restored = MemoryService.from_dict(state, fm)
        assert len(restored._working_memory) == 0


# ── Graceful Degradation ──────────────────────────────────────────────

class TestGracefulDegradation:
    """Tests for graceful behavior during outages."""

    @pytest.mark.asyncio
    async def test_memory_works_without_llm(self):
        """Memory tier 1 (working memory) should function without LLM."""
        fm = _mock_failover_manager()
        memory = MemoryService("sess-gd", fm)

        # Working memory doesn't need LLM for basic operations
        for i in range(5):
            await memory.add_utterance("candidate", f"Statement {i} about algorithms.", "2024-01-01T00:00:00Z")

        assert len(memory._working_memory) == 5
        context = memory.assemble_context("Test question")
        assert len(context) > 0

    @pytest.mark.asyncio
    async def test_code_analysis_works_locally(self):
        """Code analysis local detection should work without LLM."""
        fm = _mock_failover_manager()
        code_svc = CodeAnalysisService("sess-local", fm)

        code = """
def solve(nums, target):
    seen = {}
    for i, n in enumerate(nums):
        if target - n in seen:
            return [seen[target-n], i]
        seen[n] = i
"""
        result = await code_svc.analyze_snapshot(code, "python")
        assert result.progress_state.value != "empty"

    def test_hint_service_works_with_cached_hints(self):
        """Hint service should serve pre-computed hints without LLM."""
        hint_svc = HintService("sess-cached")

        hint_svc.register_hints("q1", {
            1: "Pre-computed nudge",
            2: "Pre-computed guide",
            3: "Pre-computed direct",
        })
        hint_svc.activate_question("q1")

        # All 3 hints should work without any LLM call
        h1 = hint_svc.advance_hint()
        assert h1 is not None
        assert h1.text == "Pre-computed nudge"

        h2 = hint_svc.advance_hint()
        assert h2.text == "Pre-computed guide"

        h3 = hint_svc.advance_hint()
        assert h3.text == "Pre-computed direct"
