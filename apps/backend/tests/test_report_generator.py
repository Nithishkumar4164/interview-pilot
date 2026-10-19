"""Tests for M9 — Report Generation.

Covers:
- Report context assembly
- Report parsing from LLM output
- Hint score computation
- Fallback report generation
- Report generator end-to-end with mock LLM
"""

import pytest

from src.llm.config import TaskProviderMapping
from src.llm.failover import FailoverManager
from src.llm.health import HealthMonitor
from src.llm.interface import TaskType
from src.schemas.hint import HintResponseMetricsSchema, HintResponseQuality
from src.schemas.report import Recommendation
from src.services.analysis_service import EnhancedAnalysisResult
from src.services.contradiction_service import Contradiction
from src.services.hint_service import ActiveHint
from src.services.memory_service import FactStore
from src.services.report_generator import GeneratedReport, ReportGenerator, ReportInput


def _mock_failover_manager() -> FailoverManager:
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


def _sample_input() -> ReportInput:
    """Build a sample ReportInput for testing."""
    return ReportInput(
        session_id="test-session",
        duration_minutes=45,
        target_level="SDE-2",
        fact_store=FactStore(
            claimed_experience=["5 years Python", "AWS certified"],
            technical_positions=["Prefers microservices over monoliths"],
            key_numbers=["40% latency reduction"],
        ),
        analysis_results=[
            EnhancedAnalysisResult(answer_quality=4, on_track=True, insight="Good approach"),
            EnhancedAnalysisResult(answer_quality=3, on_track=True, insight="Adequate"),
            EnhancedAnalysisResult(answer_quality=5, on_track=True, insight="Excellent", red_flags=[]),
            EnhancedAnalysisResult(
                answer_quality=2,
                on_track=False,
                insight="Struggling",
                red_flags=["Incorrect complexity"],
            ),
        ],
        hint_events=[
            ActiveHint(
                hint_id="h1",
                question_id="q1",
                level=1,
                text="Think about data structures",
                displayed_at="2024-01-01T00:10:00",
                interviewer_used=True,
                candidate_response_quality=HintResponseQuality.FULL,
                time_to_respond_ms=15000,
            ),
            ActiveHint(
                hint_id="h2",
                question_id="q1",
                level=2,
                text="A hash map could help",
                displayed_at="2024-01-01T00:15:00",
                interviewer_used=True,
                candidate_response_quality=HintResponseQuality.PARTIAL,
                time_to_respond_ms=45000,
            ),
        ],
        hint_metrics=HintResponseMetricsSchema(
            total_hints_given=2,
            average_response_time_ms=30000.0,
            incorporation_rate=0.75,
            level_distribution={1: 1, 2: 1},
            independence_trajectory="stable",
        ),
        contradictions=[
            Contradiction(
                claim="5 years Python experience",
                new_statement="I just started learning Python",
                explanation="Conflicting claims about Python experience",
                confidence=0.8,
                suggested_probe="Can you clarify your Python experience?",
                detection_method="rule_based",
            ),
        ],
        transcript_summary="The candidate discussed their experience with distributed systems...",
        code_progress="near_complete",
        detected_approach="hash_map",
    )


# ── Report Generator ─────────────────────────────────────────────────


class TestReportGenerator:
    def setup_method(self):
        self.fm = _mock_failover_manager()
        self.generator = ReportGenerator(self.fm)

    @pytest.mark.asyncio
    async def test_generate_report(self):
        input_data = _sample_input()
        report = await self.generator.generate(input_data)

        assert isinstance(report, GeneratedReport)
        assert report.session_id == "test-session"
        assert report.recommendation in Recommendation
        assert 0 <= report.confidence_level <= 1.0
        assert len(report.scores) >= 4
        assert report.generated_at is not None
        assert report.is_final is True

    @pytest.mark.asyncio
    async def test_report_has_all_criteria(self):
        input_data = _sample_input()
        report = await self.generator.generate(input_data)

        criteria = {s.criterion for s in report.scores}
        assert "problem_solving" in criteria
        assert "code_quality" in criteria
        assert "communication" in criteria
        assert "hint_responsiveness" in criteria

    @pytest.mark.asyncio
    async def test_report_scores_valid_range(self):
        input_data = _sample_input()
        report = await self.generator.generate(input_data)

        for score in report.scores:
            assert 1 <= score.score <= 5

    @pytest.mark.asyncio
    async def test_hint_summary_computed(self):
        input_data = _sample_input()
        report = await self.generator.generate(input_data)

        assert "total_hints_given" in report.hint_summary
        assert report.hint_summary["total_hints_given"] == 2
        assert report.hint_summary["incorporation_rate"] == 0.75

    @pytest.mark.asyncio
    async def test_report_id_generated(self):
        input_data = _sample_input()
        report = await self.generator.generate(input_data)

        import uuid

        uuid.UUID(report.id)  # Should be valid UUID


# ── Context Building ─────────────────────────────────────────────────


class TestContextBuilding:
    def setup_method(self):
        self.generator = ReportGenerator(_mock_failover_manager())

    def test_context_includes_basic_info(self):
        input_data = _sample_input()
        ctx = self.generator._build_context(input_data)

        assert "SDE-2" in ctx
        assert "45 minutes" in ctx
        assert "test-session" in ctx

    def test_context_includes_facts(self):
        input_data = _sample_input()
        ctx = self.generator._build_context(input_data)

        assert "5 years Python" in ctx
        assert "AWS certified" in ctx

    def test_context_includes_analysis(self):
        input_data = _sample_input()
        ctx = self.generator._build_context(input_data)

        assert "Average answer quality" in ctx
        assert "3.5" in ctx  # (4+3+5+2)/4

    def test_context_includes_hints(self):
        input_data = _sample_input()
        ctx = self.generator._build_context(input_data)

        assert "Hint Usage" in ctx
        assert "Total hints given: 2" in ctx

    def test_context_includes_contradictions(self):
        input_data = _sample_input()
        ctx = self.generator._build_context(input_data)

        assert "Contradictions Detected" in ctx
        assert "Conflicting claims" in ctx

    def test_context_includes_code(self):
        input_data = _sample_input()
        ctx = self.generator._build_context(input_data)

        assert "near_complete" in ctx
        assert "hash_map" in ctx

    def test_parse_key_moment_missing_type_defaults_to_none(self):
        # Regression: previously defaulted to "neutral" which is not in
        # the documented enum (breakthrough|struggle|hint|contradiction|
        # code_milestone|strong_answer|weak_answer). Missing/unknown type
        # now becomes None so consumers fall back on sentiment.
        llm_payload = {
            "scores": [],
            "key_moments": [
                {
                    "timestamp": "00:05:00",
                    "title": "Candidate paused",
                    "description": "Long silence before answering",
                    "sentiment": "negative",
                    "impact_on_assessment": "Minor",
                    # type omitted
                },
                {
                    "timestamp": "00:10:00",
                    "title": "Invalid type",
                    "description": "...",
                    "sentiment": "positive",
                    "impact_on_assessment": "Positive",
                    "type": "some_bogus_type",
                },
                {
                    "timestamp": "00:15:00",
                    "title": "Known good",
                    "description": "Right call",
                    "sentiment": "positive",
                    "impact_on_assessment": "Strong",
                    "type": "breakthrough",
                },
            ],
            "strengths": [],
            "areas_for_growth": [],
            "level_comparison": "",
            "recommendation": "lean_hire",
            "confidence_level": 0.7,
        }
        input_data = _sample_input()
        report = self.generator._parse_report("", llm_payload, input_data)

        assert len(report.key_moments) == 3
        assert report.key_moments[0].type is None
        assert report.key_moments[1].type is None
        assert report.key_moments[2].type == "breakthrough"


# ── Hint Score Computation ───────────────────────────────────────────


class TestHintScoreComputation:
    def setup_method(self):
        self.generator = ReportGenerator(_mock_failover_manager())

    def test_no_hints_neutral_score(self):
        metrics = HintResponseMetricsSchema(
            total_hints_given=0,
            average_response_time_ms=None,
            incorporation_rate=0.0,
            level_distribution={},
            independence_trajectory="stable",
        )
        assert self.generator.compute_hint_score(metrics) == 3

    def test_high_incorporation_high_score(self):
        metrics = HintResponseMetricsSchema(
            total_hints_given=5,
            average_response_time_ms=10000,
            incorporation_rate=1.0,
            level_distribution={1: 5},
            independence_trajectory="improving",
        )
        score = self.generator.compute_hint_score(metrics)
        assert score == 5

    def test_low_incorporation_low_score(self):
        metrics = HintResponseMetricsSchema(
            total_hints_given=5,
            average_response_time_ms=150000,
            incorporation_rate=0.0,
            level_distribution={3: 5},
            independence_trajectory="declining",
        )
        score = self.generator.compute_hint_score(metrics)
        assert score <= 2

    def test_moderate_performance(self):
        metrics = HintResponseMetricsSchema(
            total_hints_given=3,
            average_response_time_ms=60000,
            incorporation_rate=0.5,
            level_distribution={1: 1, 2: 2},
            independence_trajectory="stable",
        )
        score = self.generator.compute_hint_score(metrics)
        assert 3 <= score <= 4


# ── Fallback Report ──────────────────────────────────────────────────


class TestFallbackReport:
    def setup_method(self):
        self.generator = ReportGenerator(_mock_failover_manager())

    def test_fallback_produces_valid_report(self):
        input_data = _sample_input()
        report = self.generator._fallback_report(input_data)

        assert report.session_id == "test-session"
        assert report.recommendation == Recommendation.LEAN_HIRE
        assert report.confidence_level == 0.3
        # Schema currently has 7 criteria (see ARCHITECTURE.md §4.4).
        assert len(report.scores) == 7

    def test_fallback_uses_analysis_average(self):
        input_data = _sample_input()
        report = self.generator._fallback_report(input_data)

        # Average of [4,3,5,2] = 3.5 → rounds to 4
        ps_score = next(s for s in report.scores if s.criterion == "problem_solving")
        assert ps_score.score == 4  # ceil(3.5)

    def test_fallback_empty_analysis(self):
        input_data = _sample_input()
        input_data.analysis_results = []
        report = self.generator._fallback_report(input_data)

        # Default score of 3
        ps_score = next(s for s in report.scores if s.criterion == "problem_solving")
        assert ps_score.score == 3
