"""Report generation engine.

Aggregates all session data and produces a comprehensive interview
assessment using LLM analysis.

Input: ReportInput (transcript, analysis, code, facts, hints, contradictions)
Output: Structured report with recommendation, scores, key moments
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.schemas.hint import HintResponseMetricsSchema, HintResponseQuality
from src.schemas.report import (
    AssessmentSchema,
    CriterionScoreSchema,
    KeyMomentSchema,
    Recommendation,
)
from src.services.analysis_service import AnalysisResult
from src.services.contradiction_service import Contradiction
from src.services.hint_service import ActiveHint
from src.services.memory_service import FactStore
from src.utils.logging import get_logger

logger = get_logger("report_generator")

REPORT_SYSTEM_PROMPT = """You are an expert interview evaluator generating a comprehensive post-interview report.

Given the interview data below, produce a structured assessment in JSON format:

{{
  "recommendation": "strong_hire|hire|lean_hire|lean_no_hire|no_hire",
  "confidence_level": 0.0-1.0,
  "scores": [
    {{
      "criterion": "problem_solving",
      "score": 1-5,
      "evidence": ["specific examples from transcript"],
      "trend": "improving|stable|declining"
    }},
    {{
      "criterion": "code_quality",
      "score": 1-5,
      "evidence": ["specific examples"],
      "trend": "improving|stable|declining"
    }},
    {{
      "criterion": "communication",
      "score": 1-5,
      "evidence": ["specific examples"],
      "trend": "improving|stable|declining"
    }},
    {{
      "criterion": "hint_responsiveness",
      "score": 1-5,
      "evidence": ["specific examples"],
      "trend": "improving|stable|declining"
    }}
  ],
  "key_moments": [
    {{
      "timestamp": "HH:MM:SS",
      "title": "Brief title",
      "description": "What happened",
      "sentiment": "positive|negative|neutral",
      "impact_on_assessment": "How this affected the overall assessment"
    }}
  ],
  "strengths": ["list of 3-5 strengths with evidence"],
  "areas_for_growth": ["list of 2-4 areas with constructive framing"],
  "level_comparison": "How the candidate compares to the target level expectations"
}}

Score scale: 1=Poor, 2=Below Average, 3=Average, 4=Good, 5=Excellent

Base your assessment ONLY on the evidence provided. Be specific with examples."""


@dataclass
class ReportInput:
    """All data needed to generate a report."""

    session_id: str
    duration_minutes: float
    target_level: str
    fact_store: FactStore
    analysis_results: list[AnalysisResult]
    hint_events: list[ActiveHint]
    hint_metrics: HintResponseMetricsSchema
    contradictions: list[Contradiction]
    transcript_summary: str
    code_progress: str
    detected_approach: str | None = None


@dataclass
class GeneratedReport:
    """Complete generated report."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    recommendation: Recommendation = Recommendation.LEAN_HIRE
    confidence_level: float = 0.0
    scores: list[CriterionScoreSchema] = field(default_factory=list)
    key_moments: list[KeyMomentSchema] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    areas_for_growth: list[str] = field(default_factory=list)
    level_comparison: str = ""
    hint_summary: dict = field(default_factory=dict)
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    is_final: bool = True


class ReportGenerator:
    """Generates comprehensive interview reports.

    Usage:
        generator = ReportGenerator(failover_manager)
        report = await generator.generate(report_input)
    """

    def __init__(self, failover_manager: FailoverManager) -> None:
        self._failover = failover_manager

    async def generate(self, input_data: ReportInput) -> GeneratedReport:
        """Generate a complete interview report from session data."""

        # Build the context for LLM analysis
        context = self._build_context(input_data)

        try:
            result = await self._failover.analyze(
                AnalysisContext(
                    system_prompt=REPORT_SYSTEM_PROMPT,
                    user_content=context,
                    task_type=TaskType.REPORT_GENERATION,
                ),
                LLMConfig(
                    max_tokens=2000,
                    temperature=0.3,
                    response_format="json",
                ),
                session_id=input_data.session_id,
            )

            report = self._parse_report(
                result.content,
                result.parsed,
                input_data,
            )

        except Exception as e:
            logger.error(
                "report_generation_failed",
                session_id=input_data.session_id,
                error=str(e),
            )
            # Return a minimal report
            report = self._fallback_report(input_data)

        # Compute hint summary
        report.hint_summary = self._compute_hint_summary(input_data)

        return report

    def _build_context(self, input_data: ReportInput) -> str:
        """Assemble the full context for report generation."""
        parts = [
            f"## Interview Details",
            f"- Target Level: {input_data.target_level}",
            f"- Duration: {input_data.duration_minutes:.0f} minutes",
            f"- Session ID: {input_data.session_id}",
        ]

        # Facts
        facts_text = input_data.fact_store.to_text()
        if facts_text:
            parts.append(f"\n## Key Facts Extracted\n{facts_text}")

        # Transcript summary
        if input_data.transcript_summary:
            parts.append(f"\n## Interview Summary\n{input_data.transcript_summary}")

        # Analysis highlights
        if input_data.analysis_results:
            avg_quality = sum(a.answer_quality for a in input_data.analysis_results if a.answer_quality > 0)
            count = sum(1 for a in input_data.analysis_results if a.answer_quality > 0)
            if count > 0:
                avg = avg_quality / count
                parts.append(f"\n## Real-Time Analysis Summary")
                parts.append(f"- Average answer quality: {avg:.1f}/5")
                parts.append(f"- Total analysis points: {len(input_data.analysis_results)}")

                # Red flags
                all_flags = [f for a in input_data.analysis_results for f in a.red_flags]
                if all_flags:
                    parts.append(f"- Red flags: {'; '.join(all_flags[:10])}")

                # Key insights
                insights = [a.insight for a in input_data.analysis_results if a.insight][-5:]
                if insights:
                    parts.append(f"- Recent insights: {'; '.join(insights)}")

        # Code analysis
        if input_data.code_progress:
            parts.append(f"\n## Code Analysis")
            parts.append(f"- Final progress: {input_data.code_progress}")
            if input_data.detected_approach:
                parts.append(f"- Detected approach: {input_data.detected_approach}")

        # Hints
        if input_data.hint_events:
            parts.append(f"\n## Hint Usage")
            parts.append(f"- Total hints given: {len(input_data.hint_events)}")
            parts.append(f"- Incorporation rate: {input_data.hint_metrics.incorporation_rate:.0%}")
            parts.append(f"- Independence trajectory: {input_data.hint_metrics.independence_trajectory}")
            if input_data.hint_metrics.average_response_time_ms:
                parts.append(f"- Average response time: {input_data.hint_metrics.average_response_time_ms:.0f}ms")

        # Contradictions
        if input_data.contradictions:
            parts.append(f"\n## Contradictions Detected")
            for c in input_data.contradictions[:5]:
                parts.append(f"- [{c.confidence:.0%}] {c.explanation}")

        return "\n".join(parts)

    def _parse_report(
        self,
        content: str,
        parsed: dict | None,
        input_data: ReportInput,
    ) -> GeneratedReport:
        """Parse the LLM response into a GeneratedReport."""
        data = parsed
        if not data:
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return self._fallback_report(input_data)

        # Parse scores
        scores = []
        for s in data.get("scores", []):
            scores.append(CriterionScoreSchema(
                criterion=s.get("criterion", "unknown"),
                score=min(5, max(1, int(s.get("score", 3)))),
                evidence=s.get("evidence", []),
                trend=s.get("trend", "stable"),
            ))

        # Ensure we have all 4 criteria
        criteria_present = {s.criterion for s in scores}
        for required in ("problem_solving", "code_quality", "communication", "hint_responsiveness"):
            if required not in criteria_present:
                scores.append(CriterionScoreSchema(
                    criterion=required, score=3, evidence=[], trend="stable",
                ))

        # Parse key moments
        key_moments = []
        for m in data.get("key_moments", []):
            key_moments.append(KeyMomentSchema(
                timestamp=m.get("timestamp", "00:00:00"),
                title=m.get("title", ""),
                description=m.get("description", ""),
                sentiment=m.get("sentiment", "neutral"),
                impact_on_assessment=m.get("impact_on_assessment", ""),
            ))

        # Parse recommendation
        try:
            recommendation = Recommendation(data.get("recommendation", "lean_hire"))
        except ValueError:
            recommendation = Recommendation.LEAN_HIRE

        return GeneratedReport(
            session_id=input_data.session_id,
            recommendation=recommendation,
            confidence_level=min(1.0, max(0.0, float(data.get("confidence_level", 0.5)))),
            scores=scores,
            key_moments=key_moments,
            strengths=data.get("strengths", []),
            areas_for_growth=data.get("areas_for_growth", []),
            level_comparison=data.get("level_comparison", ""),
        )

    def _fallback_report(self, input_data: ReportInput) -> GeneratedReport:
        """Generate a minimal report when LLM fails."""
        # Compute basic scores from analysis data
        avg_quality = 3
        if input_data.analysis_results:
            scored = [a for a in input_data.analysis_results if a.answer_quality > 0]
            if scored:
                avg_quality = round(sum(a.answer_quality for a in scored) / len(scored))

        return GeneratedReport(
            session_id=input_data.session_id,
            recommendation=Recommendation.LEAN_HIRE,
            confidence_level=0.3,
            scores=[
                CriterionScoreSchema(criterion="problem_solving", score=avg_quality, evidence=[], trend="stable"),
                CriterionScoreSchema(criterion="code_quality", score=avg_quality, evidence=[], trend="stable"),
                CriterionScoreSchema(criterion="communication", score=avg_quality, evidence=[], trend="stable"),
                CriterionScoreSchema(criterion="hint_responsiveness", score=3, evidence=[], trend="stable"),
            ],
            strengths=["Report generated with limited data"],
            areas_for_growth=["Full LLM analysis was unavailable"],
            level_comparison="Unable to compare due to limited analysis",
        )

    def _compute_hint_summary(self, input_data: ReportInput) -> dict:
        """Compute the hint interaction summary for the report."""
        metrics = input_data.hint_metrics
        hint_details = []
        for h in input_data.hint_events:
            hint_details.append({
                "question_id": h.question_id,
                "level": h.level,
                "interviewer_used": h.interviewer_used,
                "response_quality": h.candidate_response_quality.value,
                "time_to_respond_ms": h.time_to_respond_ms,
            })

        return {
            "total_hints_given": metrics.total_hints_given,
            "incorporation_rate": metrics.incorporation_rate,
            "average_response_time_ms": metrics.average_response_time_ms,
            "independence_trajectory": metrics.independence_trajectory,
            "level_distribution": metrics.level_distribution,
            "details": hint_details,
        }

    def compute_hint_score(self, metrics: HintResponseMetricsSchema) -> int:
        """Compute the hint responsiveness score (1-5) from metrics.

        Scoring criteria:
        - Incorporation rate: how often hints were picked up
        - Response time: how quickly candidate acted on hints
        - Independence: whether candidate needed fewer/lower hints over time
        """
        if metrics.total_hints_given == 0:
            return 3  # Neutral — no hints needed

        score = 3.0  # Base

        # Incorporation rate bonus (0-1.5)
        score += metrics.incorporation_rate * 1.5

        # Response time bonus (fast = good)
        if metrics.average_response_time_ms is not None:
            if metrics.average_response_time_ms < 30000:  # < 30s
                score += 0.5
            elif metrics.average_response_time_ms > 120000:  # > 2 min
                score -= 0.5

        # Independence trajectory
        if metrics.independence_trajectory == "improving":
            score += 0.5
        elif metrics.independence_trajectory == "declining":
            score -= 0.5

        # High-level hints needed = penalty
        level_3_ratio = metrics.level_distribution.get(3, 0) / max(1, metrics.total_hints_given)
        if level_3_ratio > 0.5:
            score -= 0.5

        return min(5, max(1, round(score)))
