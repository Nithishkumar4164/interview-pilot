"""Tests for M5 — Memory & Analysis Engine.

Covers:
- Token budget utilities
- Hierarchical memory service (3-tier)
- Fact store operations
- Contradiction detection (rule-based + LLM)
- Analysis service (context assembly, structured output)
- Analysis worker (event flow, rate limiting)
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.config import TaskProviderMapping
from src.llm.failover import FailoverManager
from src.llm.health import HealthMonitor
from src.llm.interface import AnalysisContext, LLMConfig, StructuredOutput, TaskType
from src.llm.providers.mock import MockProvider
from src.schemas.transcript import SpeakerRole, TranscriptEventSchema
from src.services.analysis_service import AnalysisResult, AnalysisService
from src.services.contradiction_service import ContradictionService
from src.services.memory_service import (
    FactStore,
    MemoryService,
    SUMMARY_TRIGGER_COUNT,
    WORKING_MEMORY_SIZE,
    Utterance,
)
from src.utils.token_budget import (
    BUDGET_TOTAL,
    estimate_tokens,
    truncate_to_budget,
)
from src.workers.analysis_worker import AnalysisWorker


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
        fs.merge({
            "claimed_experience": ["5 years Python", "AWS certified"],
            "technical_positions": ["Prefers microservices"],
        })
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


class TestMemoryService:
    def setup_method(self):
        self.fm = _mock_failover_manager()
        self.memory = MemoryService("test-session", self.fm)

    @pytest.mark.asyncio
    async def test_add_utterance_to_working_memory(self):
        await self.memory.add_utterance("candidate", "Hello world", "2024-01-01T00:00:00")
        assert len(self.memory._working_memory) == 1
        assert self.memory._total_utterances == 1

    @pytest.mark.asyncio
    async def test_working_memory_fifo_eviction(self):
        for i in range(WORKING_MEMORY_SIZE + 5):
            await self.memory.add_utterance("candidate", f"Utterance {i}", f"2024-01-01T00:00:{i:02d}")
        assert len(self.memory._working_memory) == WORKING_MEMORY_SIZE
        # Oldest should be evicted
        assert self.memory._working_memory[0].text == f"Utterance 5"

    @pytest.mark.asyncio
    async def test_empty_utterance_ignored(self):
        await self.memory.add_utterance("candidate", "  ", "2024-01-01T00:00:00")
        assert len(self.memory._working_memory) == 0

    @pytest.mark.asyncio
    async def test_summary_triggered_after_threshold(self):
        for i in range(SUMMARY_TRIGGER_COUNT):
            await self.memory.add_utterance(
                "candidate", f"Utterance number {i} with some content", f"2024-01-01T00:00:{i:02d}"
            )
        # Summary should have been compressed (mock returns canned text)
        assert self.memory._rolling_summary != ""
        assert len(self.memory._unsummarized_buffer) == 0

    @pytest.mark.asyncio
    async def test_fact_extraction_on_long_utterance(self):
        # Need > 20 words
        long_text = "I have been working with Python for five years at Google where I built a distributed system that processed millions of requests per second and reduced latency by forty percent"
        await self.memory.add_utterance("candidate", long_text, "2024-01-01T00:00:00")
        # MockProvider returns canned real_time_analysis response which doesn't have fact fields
        # but the extraction shouldn't crash
        assert self.memory._total_utterances == 1

    @pytest.mark.asyncio
    async def test_assemble_context_empty(self):
        ctx = self.memory.assemble_context()
        assert "(No facts extracted yet)" in ctx

    @pytest.mark.asyncio
    async def test_assemble_context_with_data(self):
        await self.memory.add_utterance("candidate", "I use Python", "2024-01-01T00:00:00")
        self.memory.fact_store.merge({"claimed_experience": ["Python expert"]})

        ctx = self.memory.assemble_context(
            question_text="Implement two sum",
            hint_text="Think about hash maps",
        )
        assert "Python expert" in ctx
        assert "I use Python" in ctx
        assert "Implement two sum" in ctx
        assert "Think about hash maps" in ctx

    @pytest.mark.asyncio
    async def test_hint_interaction_recording(self):
        self.memory.add_hint_interaction({"level": 1, "used": True})
        assert len(self.memory.fact_store.hint_interactions) == 1

    @pytest.mark.asyncio
    async def test_serialization_roundtrip(self):
        await self.memory.add_utterance("candidate", "Test utterance", "2024-01-01T00:00:00")
        self.memory.fact_store.merge({"claimed_experience": ["Expert"]})

        data = self.memory.to_dict()
        restored = MemoryService.from_dict(data, self.fm)

        assert len(restored._working_memory) == 1
        assert restored.fact_store.claimed_experience == ["Expert"]


# ── Contradiction Service ────────────────────────────────────────────


class TestContradictionService:
    def setup_method(self):
        self.fm = _mock_failover_manager()
        self.service = ContradictionService("test-session", self.fm)

    @pytest.mark.asyncio
    async def test_rule_based_experience_contradiction(self):
        fact_store = FactStore(
            claimed_experience=["I have experience with Python and Django"],
        )
        contradictions = await self.service.check(
            "I've never used Django before", fact_store,
        )
        assert len(contradictions) == 1
        assert contradictions[0].detection_method == "rule_based"
        assert contradictions[0].confidence == 0.8

    @pytest.mark.asyncio
    async def test_rule_based_knowledge_contradiction(self):
        fact_store = FactStore(
            claimed_experience=["I know Kubernetes well"],
        )
        contradictions = await self.service.check(
            "I don't know Kubernetes at all", fact_store,
        )
        assert len(contradictions) == 1

    @pytest.mark.asyncio
    async def test_no_contradiction(self):
        fact_store = FactStore(
            claimed_experience=["I have experience with Python"],
        )
        contradictions = await self.service.check(
            "I also have experience with Go", fact_store,
        )
        assert len(contradictions) == 0

    @pytest.mark.asyncio
    async def test_skip_duplicate_pairs(self):
        fact_store = FactStore(
            claimed_experience=["I have experience with Python"],
        )
        await self.service.check("I've never used Python", fact_store)
        # Check same pair again — should skip
        result = await self.service.check("I've never used Python", fact_store)
        assert len(result) == 0
        # But total contradictions should still be 1
        assert len(self.service.contradictions) == 1

    @pytest.mark.asyncio
    async def test_empty_fact_store(self):
        fact_store = FactStore()
        contradictions = await self.service.check("anything", fact_store)
        assert len(contradictions) == 0

    @pytest.mark.asyncio
    async def test_serialization(self):
        fact_store = FactStore(
            claimed_experience=["I have experience with React"],
        )
        await self.service.check("I've never used React", fact_store)
        data = self.service.to_dict()
        assert len(data["contradictions"]) == 1
        assert "claim" in data["contradictions"][0]


# ── Analysis Service ─────────────────────────────────────────────────


class TestAnalysisService:
    def setup_method(self):
        self.fm = _mock_failover_manager()
        self.memory = MemoryService("test-session", self.fm)
        self.analysis = AnalysisService("test-session", self.fm, self.memory)

    @pytest.mark.asyncio
    async def test_analyze_utterance(self):
        await self.memory.add_utterance("candidate", "I would use a hash map approach", "2024-01-01T00:00:00")
        result = await self.analysis.analyze_utterance("candidate", "I would use a hash map approach")

        assert isinstance(result, AnalysisResult)
        assert 1 <= result.answer_quality <= 5
        assert isinstance(result.on_track, bool)
        assert result.insight != ""

    @pytest.mark.asyncio
    async def test_analyze_with_question_context(self):
        self.analysis.set_current_question("Implement two sum")
        self.analysis.set_active_hint("Think about data structures")

        result = await self.analysis.analyze_utterance("candidate", "I'll use a hash map")
        assert result.answer_quality > 0

    @pytest.mark.asyncio
    async def test_analysis_history(self):
        await self.analysis.analyze_utterance("candidate", "Response 1")
        await self.analysis.analyze_utterance("candidate", "Response 2")

        recent = self.analysis.get_recent_analyses(5)
        assert len(recent) == 2

    @pytest.mark.asyncio
    async def test_average_quality(self):
        await self.analysis.analyze_utterance("candidate", "Good response")
        avg = self.analysis.get_average_quality()
        assert avg > 0

    @pytest.mark.asyncio
    async def test_serialization(self):
        await self.analysis.analyze_utterance("candidate", "Test")
        data = self.analysis.to_dict()
        assert data["analysis_count"] == 1
        assert "recent_analyses" in data

    @pytest.mark.asyncio
    async def test_parse_invalid_json_fallback(self):
        result = self.analysis._parse_analysis("not json at all", None)
        assert result.insight == "not json at all"[:200]


# ── Analysis Worker ──────────────────────────────────────────────────


class TestAnalysisWorker:
    def setup_method(self):
        self.fm = _mock_failover_manager()
        self.worker = AnalysisWorker("test-session", self.fm)

    @pytest.mark.asyncio
    async def test_process_transcript_event(self):
        event = TranscriptEventSchema(
            id="evt-1",
            session_id="test-session",
            speaker_role=SpeakerRole.CANDIDATE,
            text="I would approach this problem using a hash map to store the complements",
            is_final=True,
            confidence=0.95,
            timestamp="2024-01-01T00:00:00",
            channel="tab",
        )

        with patch("src.workers.analysis_worker.dashboard_manager") as mock_mgr:
            mock_mgr.send_transcript = AsyncMock()
            mock_mgr.send_analysis = AsyncMock()
            mock_mgr.send_red_flag = AsyncMock()
            mock_mgr.send_hint = AsyncMock()

            await self.worker.on_transcript_event(event)

            # Should push transcript to dashboard
            mock_mgr.send_transcript.assert_called_once()

        assert self.worker._events_processed == 1
        assert len(self.worker.memory._working_memory) == 1

    @pytest.mark.asyncio
    async def test_skip_short_utterances(self):
        event = TranscriptEventSchema(
            id="evt-2",
            session_id="test-session",
            speaker_role=SpeakerRole.CANDIDATE,
            text="Yes",  # Too short for analysis
            is_final=True,
            confidence=0.95,
            timestamp="2024-01-01T00:00:00",
            channel="tab",
        )

        with patch("src.workers.analysis_worker.dashboard_manager") as mock_mgr:
            mock_mgr.send_transcript = AsyncMock()
            mock_mgr.send_analysis = AsyncMock()

            await self.worker.on_transcript_event(event)

            # Transcript pushed but no analysis
            mock_mgr.send_transcript.assert_called_once()
            mock_mgr.send_analysis.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_interviewer_analysis(self):
        event = TranscriptEventSchema(
            id="evt-3",
            session_id="test-session",
            speaker_role=SpeakerRole.INTERVIEWER,
            text="Can you explain your approach to solving this problem in more detail?",
            is_final=True,
            confidence=0.95,
            timestamp="2024-01-01T00:00:00",
            channel="mic",
        )

        with patch("src.workers.analysis_worker.dashboard_manager") as mock_mgr:
            mock_mgr.send_transcript = AsyncMock()
            mock_mgr.send_analysis = AsyncMock()

            await self.worker.on_transcript_event(event)

            mock_mgr.send_transcript.assert_called_once()
            mock_mgr.send_analysis.assert_not_called()

    @pytest.mark.asyncio
    async def test_stopped_worker_ignores_events(self):
        self.worker.stop()

        event = TranscriptEventSchema(
            id="evt-4",
            session_id="test-session",
            speaker_role=SpeakerRole.CANDIDATE,
            text="This should be ignored completely by the stopped worker",
            is_final=True,
            confidence=0.95,
            timestamp="2024-01-01T00:00:00",
            channel="tab",
        )

        await self.worker.on_transcript_event(event)
        assert self.worker._events_processed == 0

    @pytest.mark.asyncio
    async def test_serialization(self):
        data = self.worker.to_dict()
        assert data["session_id"] == "test-session"
        assert data["events_processed"] == 0
        assert "memory" in data
        assert "analysis" in data
        assert "contradictions" in data
