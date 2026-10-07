"""Tests for M10 — End-to-End Integration Pipeline.

Covers the full interview lifecycle:
1. Session start
2. Transcript events → memory tier updates
3. Analysis generation → scoring
4. Code snapshot → approach detection → progress tracking
5. Hint progression → acknowledgment
6. Report generation with all data
"""

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from src.config import settings
from src.graphs.checkpointer import Checkpointer
from src.graphs.code_graph import code_thread_id
from src.llm.config import TaskProviderMapping
from src.llm.failover import FailoverManager
from src.llm.health import HealthMonitor
from src.llm.interface import TaskType
from src.schemas.hint import HintResponseQuality
from src.schemas.transcript import SpeakerRole, TranscriptEventSchema
from src.services.hint_service import HintService
from src.services.report_generator import ReportGenerator, ReportInput
from src.websocket.manager import dashboard_manager
from src.workers.langgraph_session import LangGraphSessionPipeline, thread_id_for


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


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


class _NullPersistence:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


@pytest.fixture
async def pipeline() -> AsyncIterator[LangGraphSessionPipeline]:
    if not _postgres_available():
        pytest.skip("Postgres not reachable")
    cp = Checkpointer()
    await cp.open(settings.database_url_sync)
    sid = f"e2e-{uuid.uuid4()}"
    with (
        patch.object(dashboard_manager, "send_transcript", AsyncMock()),
        patch.object(dashboard_manager, "send_analysis", AsyncMock()),
        patch.object(dashboard_manager, "send_red_flag", AsyncMock()),
        patch.object(dashboard_manager, "send_hint", AsyncMock()),
        patch.object(dashboard_manager, "send_code_analysis", AsyncMock()),
        patch("src.dependencies.get_persistence_service", lambda: _NullPersistence()),
    ):
        yield LangGraphSessionPipeline(sid, _mock_failover_manager(), cp, min_analysis_interval_s=0)
    await cp.delete_threads(thread_id_for(sid), code_thread_id(sid))
    await cp.close()


def _make_transcript(
    text: str, speaker: SpeakerRole = SpeakerRole.CANDIDATE, is_final: bool = True
) -> TranscriptEventSchema:
    return TranscriptEventSchema(
        id=f"t-{uuid.uuid4()}",
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
    """Simulates a complete interview segment through the session pipeline."""

    @pytest.mark.asyncio
    async def test_full_interview_pipeline(self, pipeline):
        """transcripts -> memory -> analysis -> code -> hints -> report inputs -> report."""
        hint_svc = HintService("sess-e2e")
        hint_svc.register_hints(
            "q1",
            {
                1: "Think about data structures.",
                2: "A hash map gives O(1) lookups.",
                3: "Map each element to its index.",
            },
        )
        hint_svc.activate_question("q1")

        await pipeline.on_question_change("Two sum", {"id": "q1", "text": "Two sum"}, 0)
        await pipeline.on_transcript_event(
            _make_transcript(
                "Given an array of integers, find two numbers that add up to a target.", SpeakerRole.INTERVIEWER
            )
        )
        candidate_utterances = [
            "I think I could use a brute force approach with two nested loops.",
            "That would be O(n squared) time complexity though.",
            "Let me think about a more efficient solution.",
            "Maybe I could sort the array first and use two pointers.",
            "Actually, a hash map approach would be even better.",
            "I can iterate through the array and for each element check if the complement exists.",
            "The time complexity would be O(n) and space O(n) as well.",
        ]
        for text in candidate_utterances:
            await pipeline.on_transcript_event(_make_transcript(text))
            await pipeline.wait_idle()

        state = pipeline.to_dict()
        assert len(state["memory"]["working_memory"]) == 8
        assert state["analyses_run"] == 7
        analyses = [call.args[1] for call in dashboard_manager.send_analysis.await_args_list]
        assert len(analyses) == 7 and all(1 <= a["answer_quality"] <= 5 for a in analyses)

        code_v1 = """
def two_sum(nums, target):
    for i in range(len(nums)):
        for j in range(i+1, len(nums)):
            if nums[i] + nums[j] == target:
                return [i, j]
"""
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
        await pipeline.on_code_snapshot(code_v1, "python", "monaco")
        await pipeline.on_code_snapshot(code_v2, "python", "monaco")
        await pipeline.wait_idle()
        progress = [call.args[1]["progress_state"] for call in dashboard_manager.send_code_analysis.await_args_list]
        assert progress[0] in ("skeleton", "partial", "near_complete")
        assert progress[-1] in ("partial", "near_complete", "complete")

        hint1 = hint_svc.advance_hint()
        assert hint1 is not None and hint1.level == 1
        hint_svc.acknowledge_hint(hint1.hint_id, True, HintResponseQuality.FULL, 15000)
        hint2 = hint_svc.advance_hint()
        assert hint2 is not None and hint2.level == 2
        metrics = hint_svc.get_metrics()
        assert metrics.total_hints_given == 2 and metrics.incorporation_rate > 0

        # What end-session hands report generation.
        snapshot = pipeline.report_snapshot()
        assert snapshot["code_progress"] == progress[-1]
        report = await ReportGenerator(_mock_failover_manager()).generate(
            ReportInput(
                session_id="sess-e2e",
                target_level="SDE-2",
                duration_minutes=45,
                transcript_summary=snapshot["rolling_summary"],
                fact_store=snapshot["fact_store"],
                analysis_results=snapshot["analysis_results"],
                hint_events=[hint1, hint2],
                hint_metrics=metrics,
                code_progress=snapshot["code_progress"],
                detected_approach=snapshot["detected_approach"],
                contradictions=snapshot["contradictions"],
            )
        )
        assert report.recommendation in ("strong_hire", "hire", "lean_hire", "lean_no_hire", "no_hire")
        assert len(report.scores) >= 4
        assert report.confidence_level > 0

    @pytest.mark.asyncio
    async def test_memory_accumulation_across_utterances(self, pipeline):
        """25 utterances: working memory capped at 10, the rest compressed into the summary."""
        for i in range(25):
            await pipeline.on_transcript_event(
                _make_transcript(f"The candidate discussed topic {i} with detail about algorithms and data structures.")
            )
            await pipeline.wait_idle()
        memory = pipeline.to_dict()["memory"]
        assert len(memory["working_memory"]) == 10
        assert memory["rolling_summary"]
        assert memory["total_utterances"] == 25

    @pytest.mark.asyncio
    async def test_code_analysis_stuck_detection(self, pipeline):
        """An editor going back and forth without net progress is reported stuck."""
        for code in ["x = 1\n" * n for n in (17, 20, 18, 20, 17, 19, 18)]:
            await pipeline.on_code_snapshot(code, "python", "monaco")
        await pipeline.wait_idle()
        stuck = [call.args[1]["is_stuck"] for call in dashboard_manager.send_code_analysis.await_args_list]
        assert stuck[-1] is True and stuck[0] is False

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
