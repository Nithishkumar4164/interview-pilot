"""Tests for M5 — Memory & Analysis Engine.

Covers:
- Token budget utilities
- Hierarchical memory service (3-tier)
- Fact store operations
- Contradiction detection (rule-based + LLM)
- Analysis service (context assembly, structured output)
- The session pipeline's memory tiers and event handling (real checkpointer)
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
from src.schemas.transcript import SpeakerRole, TranscriptEventSchema
from src.services.analysis_service import (
    EnhancedAnalysisResult,
    analyze_utterance,
    average_quality,
    parse_analysis,
)
from src.services.contradiction_service import check_contradictions
from src.services.memory_service import (
    SUMMARY_TRIGGER_COUNT,
    WORKING_MEMORY_SIZE,
    FactStore,
    Utterance,
    assemble_context,
)
from src.utils.token_budget import (
    BUDGET_TOTAL,
    estimate_tokens,
    truncate_to_budget,
)
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


# ── Token Budget ─────────────────────────────────────────────────────


class TestTokenBudget:
    def test_estimate_tokens_empty(self):
        assert estimate_tokens("") == 0

    def test_estimate_tokens_short(self):
        assert estimate_tokens("hello") >= 1

    def test_estimate_tokens_long(self):
        text = "a" * 400
        assert estimate_tokens(text) == 100

    def test_truncate_under_budget(self):
        text = "short text"
        assert truncate_to_budget(text, 100) == text

    def test_truncate_over_budget(self):
        text = "a" * 1000
        result = truncate_to_budget(text, 50)
        assert estimate_tokens(result) <= 50

    def test_truncate_keeps_recent(self):
        text = "old content " + "new content"
        result = truncate_to_budget(text, 5)
        assert "new content" in result

    def test_budget_total_reasonable(self):
        assert 4000 < BUDGET_TOTAL < 8000


# ── Fact Store ───────────────────────────────────────────────────────


class TestFactStore:
    def test_empty_fact_store(self):
        fs = FactStore()
        assert fs.to_text() == "(No facts extracted yet)"

    def test_merge_facts(self):
        fs = FactStore()
        fs.merge(
            {
                "claimed_experience": ["5 years Python", "AWS certified"],
                "technical_positions": ["Prefers microservices"],
            }
        )
        assert len(fs.claimed_experience) == 2
        assert len(fs.technical_positions) == 1

    def test_merge_deduplicates(self):
        fs = FactStore()
        fs.merge({"claimed_experience": ["5 years Python"]})
        fs.merge({"claimed_experience": ["5 years Python", "3 years Go"]})
        assert len(fs.claimed_experience) == 2

    def test_merge_skips_empty(self):
        fs = FactStore()
        fs.merge({"claimed_experience": ["", None, "valid"]})
        # Empty string and None should be skipped
        assert "valid" in fs.claimed_experience

    def test_to_text_with_data(self):
        fs = FactStore(
            claimed_experience=["5 years Python"],
            key_numbers=["40% latency reduction"],
        )
        text = fs.to_text()
        assert "5 years Python" in text
        assert "40% latency reduction" in text

    def test_serialization_roundtrip(self):
        fs = FactStore(
            claimed_experience=["Python expert"],
            technical_positions=["Prefers monoliths"],
            behavioral_signals=["Clear communicator"],
            key_numbers=["10x improvement"],
            hint_interactions=[{"level": 1, "used": True}],
        )
        data = fs.to_dict()
        restored = FactStore.from_dict(data)
        assert restored.claimed_experience == fs.claimed_experience
        assert restored.hint_interactions == fs.hint_interactions


# ── Memory Service ───────────────────────────────────────────────────


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


@pytest.fixture
async def pipeline() -> AsyncIterator[LangGraphSessionPipeline]:
    """A session pipeline (mock LLM, no rate limit) on the real checkpointer, with the dashboard patched."""
    if not _postgres_available():
        pytest.skip("Postgres not reachable")
    cp = Checkpointer()
    await cp.open(settings.database_url_sync)
    sid = f"memory-test-{uuid.uuid4()}"
    with (
        patch.object(dashboard_manager, "send_transcript", AsyncMock()),
        patch.object(dashboard_manager, "send_analysis", AsyncMock()),
        patch.object(dashboard_manager, "send_red_flag", AsyncMock()),
        patch.object(dashboard_manager, "send_hint", AsyncMock()),
        patch("src.dependencies.get_persistence_service", lambda: _NullPersistence()),
    ):
        yield LangGraphSessionPipeline(sid, _mock_failover_manager(), cp, min_analysis_interval_s=0)
    await cp.delete_threads(thread_id_for(sid), code_thread_id(sid))
    await cp.close()


class _NullPersistence:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _event(text: str, speaker: SpeakerRole = SpeakerRole.CANDIDATE, n: int = 0, final: bool = True):
    return TranscriptEventSchema(
        id=f"evt-{n}-{uuid.uuid4()}",
        session_id="test-session",
        speaker_role=speaker,
        text=text,
        is_final=final,
        confidence=0.95,
        timestamp=f"2024-01-01T00:00:{n % 60:02d}",
        channel="tab",
    )


# ── Memory tiers (session pipeline) ──────────────────────────────────


class TestMemoryTiers:
    async def test_working_memory_keeps_the_last_ten(self, pipeline) -> None:
        for i in range(WORKING_MEMORY_SIZE + 5):
            await pipeline.on_transcript_event(_event(f"Utterance {i}", SpeakerRole.INTERVIEWER, i))
        await pipeline.wait_idle()
        memory = pipeline.to_dict()["memory"]
        assert [u["text"] for u in memory["working_memory"]] == [f"Utterance {i}" for i in range(5, 15)]
        assert memory["total_utterances"] == WORKING_MEMORY_SIZE + 5

    async def test_empty_utterance_ignored(self, pipeline) -> None:
        await pipeline.on_transcript_event(_event("  ", SpeakerRole.CANDIDATE))
        await pipeline.wait_idle()
        assert pipeline.to_dict()["memory"]["working_memory"] == []

    async def test_summary_compressed_after_threshold(self, pipeline) -> None:
        for i in range(SUMMARY_TRIGGER_COUNT):
            await pipeline.on_transcript_event(
                _event(f"Utterance number {i} with some content", SpeakerRole.INTERVIEWER, i)
            )
        await pipeline.wait_idle()  # compression runs beside the graph; wait for it to land
        memory = pipeline.to_dict()["memory"]
        assert memory["rolling_summary"] != ""  # mock returns canned text
        assert memory["unsummarized_count"] == 0

    async def test_hint_acknowledgement_recorded(self, pipeline) -> None:
        await pipeline.handle_runtime_hint_ack({"hint_id": "h1", "question_id": "q1", "quality": "full"})
        await pipeline.wait_idle()
        assert pipeline.to_dict()["memory"]["fact_store"]["hint_interactions"] == [
            {"type": "runtime", "hint_id": "h1", "question_id": "q1", "quality": "full"}
        ]


class TestAssembleContext:
    def test_empty(self) -> None:
        assert "(No facts extracted yet)" in assemble_context([], "", FactStore())

    def test_with_data(self) -> None:
        facts = FactStore()
        facts.merge({"claimed_experience": ["Python expert"]})
        ctx = assemble_context(
            [Utterance(speaker="candidate", text="I use Python", timestamp="t")],
            "",
            facts,
            question_text="Implement two sum",
            hint_text="Think about hash maps",
        )
        assert "Python expert" in ctx
        assert "I use Python" in ctx
        assert "Implement two sum" in ctx
        assert "Think about hash maps" in ctx


# ── Contradictions ───────────────────────────────────────────────────


class TestContradictions:
    def setup_method(self):
        self.fm = _mock_failover_manager()

    async def _check(self, statement: str, facts: FactStore, checked: set[str] | None = None):
        return await check_contradictions(self.fm, "test-session", statement, facts, checked or set())

    async def test_rule_based_experience_contradiction(self):
        found, _ = await self._check(
            "I've never used Django before", FactStore(claimed_experience=["I have experience with Python and Django"])
        )
        assert len(found) == 1
        assert found[0].detection_method == "rule_based"
        assert found[0].confidence == 0.8

    async def test_rule_based_knowledge_contradiction(self):
        found, _ = await self._check(
            "I don't know Kubernetes at all", FactStore(claimed_experience=["I know Kubernetes well"])
        )
        assert len(found) == 1

    async def test_no_contradiction(self):
        found, _ = await self._check(
            "I also have experience with Go", FactStore(claimed_experience=["I have experience with Python"])
        )
        assert found == []

    async def test_skip_already_checked_pairs(self):
        facts = FactStore(claimed_experience=["I have experience with Python"])
        found, checked = await self._check("I've never used Python", facts)
        assert len(found) == 1
        again, _ = await self._check("I've never used Python", facts, checked)
        assert again == []

    async def test_empty_fact_store(self):
        found, _ = await self._check("anything", FactStore())
        assert found == []


# ── Utterance analysis ───────────────────────────────────────────────


class TestUtteranceAnalysis:
    def setup_method(self):
        self.fm = _mock_failover_manager()

    async def test_analyze_utterance(self):
        result = await analyze_utterance(
            self.fm,
            "test-session",
            "## Recent Conversation\n[candidate]: hi",
            None,
            "",
            "candidate",
            "I would use a hash map approach",
        )
        assert isinstance(result, EnhancedAnalysisResult)
        assert 1 <= result.answer_quality <= 5
        assert isinstance(result.on_track, bool)
        assert result.insight != ""

    async def test_analyze_with_question_and_hint(self):
        result = await analyze_utterance(
            self.fm,
            "test-session",
            "## Current Question\nImplement two sum",
            {"id": "q"},
            "Think about data structures",
            "candidate",
            "I'll use a hash map",
        )
        assert result.answer_quality > 0

    def test_average_quality_counts_scored_on_track_answers(self):
        assert average_quality([(4, True), (2, True), (5, False), (0, True)]) == 3.0
        assert average_quality([]) == 0.0

    def test_parse_invalid_json_fallback(self):
        result = parse_analysis("not json at all", None)
        assert result.insight == "not json at all"[:200]


# ── Session pipeline event handling ──────────────────────────────────


class TestPipelineEvents:
    async def test_transcript_pushed_and_remembered(self, pipeline):
        await pipeline.on_transcript_event(
            _event("I would approach this problem using a hash map to store the complements")
        )
        await pipeline.wait_idle()
        dashboard_manager.send_transcript.assert_called_once()
        dashboard_manager.send_analysis.assert_called_once()
        data = pipeline.to_dict()
        assert data["events_processed"] == 1 and len(data["memory"]["working_memory"]) == 1

    async def test_short_utterances_are_not_analyzed(self, pipeline):
        await pipeline.on_transcript_event(_event("Yes"))
        await pipeline.wait_idle()
        dashboard_manager.send_transcript.assert_called_once()
        dashboard_manager.send_analysis.assert_not_called()

    async def test_interviewer_utterances_are_not_analyzed(self, pipeline):
        await pipeline.on_transcript_event(
            _event("Can you explain your approach to solving this problem in more detail?", SpeakerRole.INTERVIEWER)
        )
        await pipeline.wait_idle()
        dashboard_manager.send_transcript.assert_called_once()
        dashboard_manager.send_analysis.assert_not_called()

    async def test_stopped_pipeline_ignores_events(self, pipeline):
        pipeline.stop()
        await pipeline.on_transcript_event(_event("This should be ignored completely by the stopped pipeline"))
        await pipeline.wait_idle()
        assert pipeline.to_dict()["events_processed"] == 0

    async def test_interim_transcripts_are_handled(self, pipeline):
        interim = _event("partial utter", final=False)
        await pipeline.on_transcript_event(interim)
        await pipeline.on_transcript_event(interim)
        await pipeline.wait_idle()
        assert pipeline.to_dict()["events_processed"] == 2
