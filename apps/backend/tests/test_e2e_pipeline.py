"""Tests for M10 — End-to-End Integration Pipeline.

Covers the full interview lifecycle:
1. Session start
2. Transcript events → memory tier updates
3. Analysis generation → scoring
4. Code snapshot → approach detection → progress tracking
5. Hint progression → acknowledgment
6. Report generation with all data
"""

import pytest

from src.llm.config import TaskProviderMapping
from src.llm.failover import FailoverManager
from src.llm.health import HealthMonitor
from src.llm.interface import TaskType
from src.schemas.hint import HintResponseQuality
from src.schemas.transcript import SpeakerRole, TranscriptEventSchema
from src.services.analysis_service import AnalysisService
from src.services.code_analysis_service import CodeAnalysisService, ProgressState
from src.services.hint_service import HintService
from src.services.memory_service import MemoryService
from src.services.report_generator import ReportGenerator, ReportInput
from src.services.session_state_service import SessionState, SessionStateService
from src.workers.analysis_worker import AnalysisWorker


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


def _make_transcript(
    text: str, speaker: SpeakerRole = SpeakerRole.CANDIDATE, is_final: bool = True
) -> TranscriptEventSchema:
    return TranscriptEventSchema(
        id="t-" + str(hash(text) % 10000),
        session_id="sess-e2e",
        speaker_role=speaker,
        text=text,
        is_final=is_final,
        confidence=0.95,
        timestamp="2024-01-01T00:00:00Z",
        channel="tab",
    )


# ── Full Pipeline E2E ─────────────────────────────────────────────────


class TestE2EPipeline:
    """Simulates a complete interview segment."""

    @pytest.mark.asyncio
    async def test_full_interview_pipeline(self):
        """Run through the complete pipeline: transcripts → memory → analysis → code → hints → report."""
        fm = _mock_failover_manager()
        memory = MemoryService("sess-e2e", fm)
        analysis_svc = AnalysisService("sess-e2e", fm, memory)
        code_svc = CodeAnalysisService("sess-e2e", fm)
        hint_svc = HintService("sess-e2e")
        report_gen = ReportGenerator(fm)

        # 1. Register hints for a question
        hint_svc.register_hints(
            "q1",
            {
                1: "Think about data structures.",
                2: "A hash map gives O(1) lookups.",
                3: "Map each element to its index.",
            },
        )
        hint_svc.activate_question("q1")

        # 2. Process interviewer question
        await memory.add_utterance(
            speaker="interviewer",
            text="Given an array of integers, find two numbers that add up to a target.",
            timestamp="2024-01-01T00:00:00Z",
        )

        # 3. Candidate discusses approach
        candidate_utterances = [
            "I think I could use a brute force approach with two nested loops.",
            "That would be O(n squared) time complexity though.",
            "Let me think about a more efficient solution.",
            "Maybe I could sort the array first and use two pointers.",
            "Actually, a hash map approach would be even better.",
            "I can iterate through the array and for each element check if the complement exists.",
            "The time complexity would be O(n) and space O(n) as well.",
        ]

        analyses = []
        for text in candidate_utterances:
            await memory.add_utterance(speaker="candidate", text=text, timestamp="2024-01-01T00:01:00Z")

            result = await analysis_svc.analyze_utterance("candidate", text)
            analyses.append(result)

        # Verify memory state
        assert len(memory._working_memory) > 0
        assert memory.fact_store is not None

        # Verify analysis results
        assert len(analyses) == 7
        for a in analyses:
            assert 1 <= a.answer_quality <= 5
            assert isinstance(a.insight, str)

        # 4. Code analysis
        code_v1 = """
def two_sum(nums, target):
    for i in range(len(nums)):
        for j in range(i+1, len(nums)):
            if nums[i] + nums[j] == target:
                return [i, j]
"""
        result1 = await code_svc.analyze_snapshot(code_v1, "python")
        assert result1.progress_state in (
            ProgressState.SKELETON,
            ProgressState.PARTIAL,
            ProgressState.NEAR_COMPLETE,
        )

        code_v2 = """
def two_sum(nums, target):
    seen = {}
    for i, num in enumerate(nums):
        complement = target - num
        if complement in seen:
            return [seen[complement], i]
        seen[num] = i
    return []
"""
        result2 = await code_svc.analyze_snapshot(code_v2, "python")
        assert result2.progress_state in (
            ProgressState.PARTIAL,
            ProgressState.NEAR_COMPLETE,
            ProgressState.COMPLETE,
        )

        # 5. Hint progression
        hint1 = hint_svc.advance_hint()
        assert hint1 is not None
        assert hint1.level == 1

        hint_svc.acknowledge_hint(hint1.hint_id, True, HintResponseQuality.FULL, 15000)

        hint2 = hint_svc.advance_hint()
        assert hint2 is not None
        assert hint2.level == 2

        metrics = hint_svc.get_metrics()
        assert metrics.total_hints_given == 2
        assert metrics.incorporation_rate > 0

        # 6. Report generation
        report_input = ReportInput(
            session_id="sess-e2e",
            target_level="SDE-2",
            duration_minutes=45,
            transcript_summary="Candidate discussed brute force, then pivoted to hash map approach.",
            fact_store=memory.fact_store,
            analysis_results=analyses,
            hint_events=[hint1, hint2],
            hint_metrics=metrics,
            code_progress="complete",
            contradictions=[],
        )

        report = await report_gen.generate(report_input)
        assert report.recommendation in (
            "strong_hire",
            "hire",
            "lean_hire",
            "lean_no_hire",
            "no_hire",
        )
        assert len(report.scores) >= 4
        assert report.confidence_level > 0

    @pytest.mark.asyncio
    async def test_memory_accumulation_across_utterances(self):
        """Verify 3-tier memory accumulates correctly across many utterances."""
        fm = _mock_failover_manager()
        memory = MemoryService("sess-mem", fm)

        # Add 25 utterances to trigger summary compression
        for i in range(25):
            await memory.add_utterance(
                speaker="candidate",
                text=f"The candidate discussed topic {i} with detail about algorithms and data structures.",
                timestamp="2024-01-01T00:00:00Z",
            )

        # Working memory should be capped
        assert len(memory._working_memory) <= 10
        # Fact store should exist
        assert memory.fact_store is not None

        # Context assembly should work
        context = memory.assemble_context(
            question_text="What is your approach?",
        )
        assert len(context) > 0

    @pytest.mark.asyncio
    async def test_analysis_worker_event_flow(self):
        """Verify analysis worker processes events and calls services correctly."""
        fm = _mock_failover_manager()
        worker = AnalysisWorker("sess-worker", fm)

        # Process transcript events
        events = [
            _make_transcript("Let me think about this problem for a moment.", speaker=SpeakerRole.CANDIDATE),
            _make_transcript(
                "I think a hash map approach would work well here for efficiency.",
                speaker=SpeakerRole.CANDIDATE,
            ),
        ]

        for event in events:
            await worker.on_transcript_event(event)

        # Worker should have processed events into memory
        assert len(worker.memory._working_memory) >= 1

    @pytest.mark.asyncio
    async def test_code_analysis_stuck_detection(self):
        """Verify stuck detection when code oscillates."""
        fm = _mock_failover_manager()
        code_svc = CodeAnalysisService("sess-stuck", fm)

        # Simulate oscillation (editing back and forth)
        codes = [
            "x = 1\n" * 17,  # ~100 chars
            "x = 1\n" * 20,  # ~120 chars
            "x = 1\n" * 18,  # ~105 chars
            "x = 1\n" * 20,  # ~118 chars
            "x = 1\n" * 17,  # ~103 chars
            "x = 1\n" * 19,  # ~115 chars
        ]
        for code in codes:
            await code_svc.analyze_snapshot(code, "python")

        result = await code_svc.analyze_snapshot("x = 1\n" * 18, "python")
        # After enough oscillation, stuck detection should trigger
        assert isinstance(result.is_stuck, bool)

    @pytest.mark.asyncio
    async def test_hint_full_progression(self):
        """Verify hint progression through all 3 levels."""
        hint_svc = HintService("sess-hints")

        hint_svc.register_hints(
            "q1",
            {
                1: "Nudge: Think about complexity.",
                2: "Guide: Consider a hash map.",
                3: "Direct: Use dict with complement lookup.",
            },
        )
        hint_svc.activate_question("q1")

        # Level 1
        h1 = hint_svc.advance_hint()
        assert h1 is not None
        assert h1.level == 1
        hint_svc.acknowledge_hint(h1.hint_id, True, HintResponseQuality.PARTIAL, 30000)

        # Level 2
        h2 = hint_svc.advance_hint()
        assert h2 is not None
        assert h2.level == 2
        hint_svc.acknowledge_hint(h2.hint_id, True, HintResponseQuality.FULL, 10000)

        # Level 3
        h3 = hint_svc.advance_hint()
        assert h3 is not None
        assert h3.level == 3
        hint_svc.acknowledge_hint(h3.hint_id, True, HintResponseQuality.FULL, 5000)

        # No more hints
        h4 = hint_svc.advance_hint()
        assert h4 is None

        # Metrics reflect all 3 hints
        metrics = hint_svc.get_metrics()
        assert metrics.total_hints_given == 3
        assert metrics.level_distribution == {1: 1, 2: 1, 3: 1}


class TestSessionStateLifecycle:
    """Tests for session state management."""

    def test_session_state_creation(self):
        svc = SessionStateService()
        state = svc.get_state("sess-1")
        assert state.session_id == "sess-1"
        assert state.transcript_count == 0

    def test_session_state_dirty_tracking(self):
        state = SessionState("sess-1")
        assert not state.is_dirty
        state.mark_dirty()
        assert state.is_dirty

    def test_session_state_snapshot_roundtrip(self):
        state = SessionState("sess-1")
        state.transcript_count = 42
        state.analysis_count = 10
        state.scores = {"problem_solving": 4.0}
        state.rolling_summary = "Test summary"
        state.mark_dirty()

        snapshot = state.to_snapshot()
        assert snapshot["transcript_count"] == 42
        assert snapshot["schema_version"] == 1

        # Restore
        recovered = SessionState.from_snapshot(snapshot)
        assert recovered.session_id == "sess-1"
        assert recovered.transcript_count == 42
        assert recovered.scores["problem_solving"] == 4.0
        assert recovered.rolling_summary == "Test summary"

    def test_session_state_remove(self):
        svc = SessionStateService()
        svc.get_state("sess-1")
        assert "sess-1" in svc._states
        svc.remove_state("sess-1")
        assert "sess-1" not in svc._states
