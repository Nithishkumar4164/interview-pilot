"""Report generation engine.

Aggregates all session data and produces a comprehensive interview
assessment using LLM analysis.

Input: ReportInput (transcript, analysis, code, facts, hints, contradictions)
Output: Structured report with recommendation, scores, key moments, annotated transcript
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.schemas.hint import HintResponseMetricsSchema
from src.schemas.report import (
    CriterionScoreSchema,
    KeyMomentSchema,
    Recommendation,
)
from src.services.memory_service import FactStore
from src.utils.logging import get_logger

logger = get_logger("report_generator")

# Output budget (thinking included) for report-generation calls.
REPORT_MAX_TOKENS = 16000

ALL_CRITERIA = (
    "problem_solving",
    "code_quality",
    "communication",
    "hint_responsiveness",
    "system_design",
    "resume_verification",
    "behavioral",
)

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
    }},
    {{
      "criterion": "system_design",
      "score": 1-5,
      "evidence": ["specific examples — architecture discussions, trade-off analysis, scalability considerations"],
      "trend": "improving|stable|declining"
    }},
    {{
      "criterion": "resume_verification",
      "score": 1-5,
      "evidence": ["consistency of claimed experience vs demonstrated knowledge, fact_store contradictions"],
      "trend": "improving|stable|declining"
    }},
    {{
      "criterion": "behavioral",
      "score": 1-5,
      "evidence": ["behavioral signals, collaboration patterns, communication style, conflict handling"],
      "trend": "improving|stable|declining"
    }}
  ],
  "key_moments": [
    {{
      "timestamp": "HH:MM:SS",
      "title": "Brief title",
      "description": "What happened",
      "sentiment": "positive|negative|neutral",
      "impact_on_assessment": "How this affected the overall assessment",
      "type": "breakthrough|struggle|hint|contradiction|code_milestone|strong_answer|weak_answer"
    }}
  ],
  "strengths": ["list of 3-5 strengths with evidence"],
  "areas_for_growth": ["list of 2-4 areas with constructive framing"],
  "level_comparison": "How the candidate compares to the target level expectations"
}}

Score scale: 1=Poor, 2=Below Average, 3=Average, 4=Good, 5=Excellent

Scoring notes:
- system_design: Score 3 if no design questions were asked (N/A). Otherwise assess architecture discussions, trade-off analysis, scalability thinking.
- resume_verification: Assess consistency between claimed experience and demonstrated knowledge. Check for contradictions in the fact_store.
- behavioral: Assess collaboration signals, communication patterns, conflict handling, and overall professionalism.

Key moments guidelines:
- Include 5-10 key moments with the appropriate type
- Include at least 1-2 hint-related moments if hints were given
- Type must be one of: breakthrough, struggle, hint, contradiction, code_milestone, strong_answer, weak_answer

Base your assessment ONLY on the evidence provided. Be specific with examples."""


@dataclass
class ReportInput:
    """All data needed to generate a report."""

    session_id: str
    duration_minutes: float
    target_level: str
    fact_store: FactStore | dict | str
    analysis_results: list  # EnhancedAnalysisResult or dict
    hint_events: list  # ActiveHint or dict
    hint_metrics: HintResponseMetricsSchema | dict
    contradictions: list  # Contradiction or dict
    transcript_summary: str
    code_progress: str
    detected_approach: str | None = None
    # Enhanced fields (Phase 1)
    full_transcript: list[dict] | None = None
    all_analysis_events: list[dict] | None = None
    code_snapshots: list[dict] | None = None
    hint_events_db: list[dict] | None = None
    time_per_question: dict[str, float] | None = None
    plan_data: dict | None = None
    failover_log: list[dict] | None = None


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
    annotated_transcript: list[dict] = field(default_factory=list)
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
        """Generate a complete interview report from session data.

        Composes the public steps below; ReportGraph runs the same steps as
        separately checkpointed nodes.
        """
        llm = await self.call_llm(input_data)
        report = self.report_from_llm(llm, input_data)
        return self.enrich(report, input_data)

    async def call_llm(self, input_data: ReportInput) -> dict:
        """One report-generation call.

        Returns ``{"content", "parsed"}`` on success or ``{"error"}`` if the
        call failed after provider failover; never raises.
        """
        try:
            result = await self._failover.analyze(
                AnalysisContext(
                    system_prompt=REPORT_SYSTEM_PROMPT,
                    user_content=self._build_context(input_data),
                    task_type=TaskType.REPORT_GENERATION,
                ),
                # Current Opus models think before answering, and thinking counts
                # toward max_tokens: 3000 truncated the report JSON.
                LLMConfig(
                    max_tokens=REPORT_MAX_TOKENS,
                    temperature=0.3,
                    response_format="json",
                ),
                session_id=input_data.session_id,
            )
            return {"content": result.content, "parsed": result.parsed}
        except Exception as e:
            logger.error(
                "report_generation_failed",
                session_id=input_data.session_id,
                error=str(e),
            )
            return {"error": str(e)}

    def report_from_llm(self, llm: dict, input_data: ReportInput) -> GeneratedReport:
        """Parse the LLM output, or build the minimal fallback report if the call failed."""
        if "error" in llm:
            return self._fallback_report(input_data)
        try:
            return self._parse_report(llm["content"], llm.get("parsed"), input_data)
        except Exception as e:
            # Same outcome as a failed call (the original single try covered both).
            logger.error("report_parse_failed", session_id=input_data.session_id, error=str(e))
            return self._fallback_report(input_data)

    def enrich(self, report: GeneratedReport, input_data: ReportInput) -> GeneratedReport:
        """Attach the deterministic parts: hint summary and annotated transcript."""
        report.hint_summary = self._compute_hint_summary(input_data)
        report.annotated_transcript = self._build_annotated_transcript(input_data)
        return report

    def _build_context(self, input_data: ReportInput) -> str:
        """Assemble the full context for report generation."""
        parts = [
            "## Interview Details",
            f"- Target Level: {input_data.target_level}",
            f"- Duration: {input_data.duration_minutes:.0f} minutes",
            f"- Session ID: {input_data.session_id}",
        ]

        # Facts
        if isinstance(input_data.fact_store, FactStore):
            facts_text = input_data.fact_store.to_text()
        elif isinstance(input_data.fact_store, str):
            facts_text = input_data.fact_store
        elif isinstance(input_data.fact_store, dict):
            facts_text = json.dumps(input_data.fact_store, indent=2)
        else:
            facts_text = ""
        if facts_text:
            parts.append(f"\n## Key Facts Extracted\n{facts_text}")

        # Transcript summary
        if input_data.transcript_summary:
            parts.append(f"\n## Interview Summary\n{input_data.transcript_summary}")

        # Full transcript excerpts (token-budgeted)
        if input_data.full_transcript:
            transcript_lines = []
            for t in input_data.full_transcript:
                speaker = t.get("speaker_role", "unknown")
                text = t.get("text", "")
                ts = t.get("timestamp", "")
                transcript_lines.append(f"[{ts}] {speaker}: {text}")

            combined = "\n".join(transcript_lines)
            # Budget: first ~3000 chars + last ~3000 chars
            if len(combined) > 7000:
                first = combined[:3000]
                last = combined[-3000:]
                combined = first + "\n\n... [transcript truncated] ...\n\n" + last

            parts.append(f"\n## Transcript Excerpts\n{combined}")

        # Time per question
        if input_data.time_per_question:
            parts.append("\n## Time Per Question")
            for q_id, mins in input_data.time_per_question.items():
                parts.append(f"- {q_id}: {mins:.1f} minutes")

        # Analysis highlights
        analysis_results = input_data.analysis_results
        if analysis_results:
            if isinstance(analysis_results[0], dict):
                quality_vals = [a.get("answer_quality", 0) for a in analysis_results]
                scored = [q for q in quality_vals if q > 0]
            else:
                scored = [a.answer_quality for a in analysis_results if a.answer_quality > 0]
            if scored:
                avg = sum(scored) / len(scored)
                parts.append("\n## Real-Time Analysis Summary")
                parts.append(f"- Average answer quality: {avg:.1f}/5")
                parts.append(f"- Total analysis points: {len(analysis_results)}")

                # Red flags
                if isinstance(analysis_results[0], dict):
                    all_flags = [f for a in analysis_results for f in (a.get("red_flags") or [])]
                else:
                    all_flags = [f for a in analysis_results for f in a.red_flags]
                if all_flags:
                    parts.append(f"- Red flags: {'; '.join(all_flags[:10])}")

                # Key insights
                if isinstance(analysis_results[0], dict):
                    insights = [a.get("insight", "") for a in analysis_results if a.get("insight")][-5:]
                else:
                    insights = [a.insight for a in analysis_results if a.insight][-5:]
                if insights:
                    parts.append(f"- Recent insights: {'; '.join(insights)}")

        # Code analysis
        if input_data.code_progress:
            parts.append("\n## Code Analysis")
            parts.append(f"- Final progress: {input_data.code_progress}")
            if input_data.detected_approach:
                parts.append(f"- Detected approach: {input_data.detected_approach}")

        # Code progression timeline
        if input_data.code_snapshots:
            parts.append("\n## Code Progression Timeline")
            for cs in input_data.code_snapshots[-10:]:
                ts = cs.get("timestamp", "")
                state = cs.get("progress_state", "unknown")
                approach = cs.get("approach", "")
                parts.append(f"- [{ts}] {state}" + (f" ({approach})" if approach else ""))

        # Hints
        hint_events = input_data.hint_events
        if hint_events:
            parts.append("\n## Hint Usage")
            parts.append(f"- Total hints given: {len(hint_events)}")
            if hasattr(input_data.hint_metrics, "incorporation_rate"):
                parts.append(f"- Incorporation rate: {input_data.hint_metrics.incorporation_rate:.0%}")
                parts.append(f"- Independence trajectory: {input_data.hint_metrics.independence_trajectory}")
                if input_data.hint_metrics.average_response_time_ms:
                    parts.append(f"- Average response time: {input_data.hint_metrics.average_response_time_ms:.0f}ms")
            elif isinstance(input_data.hint_metrics, dict):
                parts.append(f"- Incorporation rate: {input_data.hint_metrics.get('incorporation_rate', 0):.0%}")
                parts.append(
                    f"- Independence trajectory: {input_data.hint_metrics.get('independence_trajectory', 'unknown')}"
                )

        # Contradictions
        contradictions = input_data.contradictions
        if contradictions:
            parts.append("\n## Contradictions Detected")
            for c in contradictions[:5]:
                if isinstance(c, dict):
                    parts.append(f"- [{c.get('confidence', 0):.0%}] {c.get('explanation', '')}")
                else:
                    parts.append(f"- [{c.confidence:.0%}] {c.explanation}")

        # Resume Verification section (for new criterion)
        if isinstance(input_data.fact_store, FactStore):
            claimed = getattr(input_data.fact_store, "claimed_experience", [])
            if claimed:
                parts.append("\n## Resume Verification")
                parts.append("Claimed experience:")
                for item in claimed[:10]:
                    parts.append(f"- {item}")
        elif isinstance(input_data.fact_store, dict):
            claimed = input_data.fact_store.get("claimed_experience", [])
            if claimed:
                parts.append("\n## Resume Verification")
                parts.append("Claimed experience:")
                for item in claimed[:10]:
                    parts.append(f"- {item}")

        # Behavioral Signals section
        if isinstance(input_data.fact_store, FactStore):
            behavioral = getattr(input_data.fact_store, "behavioral_signals", [])
            if behavioral:
                parts.append("\n## Behavioral Signals")
                for signal in behavioral[:10]:
                    parts.append(f"- {signal}")
        elif isinstance(input_data.fact_store, dict):
            behavioral = input_data.fact_store.get("behavioral_signals", [])
            if behavioral:
                parts.append("\n## Behavioral Signals")
                for signal in behavioral[:10]:
                    parts.append(f"- {signal}")

        # Plan adherence
        if input_data.plan_data:
            parts.append("\n## Interview Plan")
            phases = input_data.plan_data.get("phases", [])
            for phase in phases[:5]:
                phase_name = phase.get("phase", "")
                questions = phase.get("questions", [])
                q_texts = [q.get("text", "")[:80] for q in questions[:5]]
                parts.append(f"- {phase_name}: {', '.join(q_texts)}")

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
            scores.append(
                CriterionScoreSchema(
                    criterion=s.get("criterion", "unknown"),
                    score=min(5, max(1, int(s.get("score", 3)))),
                    evidence=s.get("evidence", []),
                    trend=s.get("trend", "stable"),
                )
            )

        # Ensure all 7 criteria present
        criteria_present = {s.criterion for s in scores}
        for required in ALL_CRITERIA:
            if required not in criteria_present:
                scores.append(
                    CriterionScoreSchema(
                        criterion=required,
                        score=3,
                        evidence=[],
                        trend="stable",
                    )
                )

        # Parse key moments
        valid_moment_types = {
            "breakthrough",
            "struggle",
            "hint",
            "contradiction",
            "code_milestone",
            "strong_answer",
            "weak_answer",
        }
        key_moments = []
        for m in data.get("key_moments", []):
            raw_type = m.get("type")
            moment_type = raw_type if raw_type in valid_moment_types else None
            key_moments.append(
                KeyMomentSchema(
                    timestamp=m.get("timestamp", "00:00:00"),
                    title=m.get("title", ""),
                    description=m.get("description", ""),
                    sentiment=m.get("sentiment", "neutral"),
                    impact_on_assessment=m.get("impact_on_assessment", ""),
                    type=moment_type,
                )
            )

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
        analysis_results = input_data.analysis_results
        if analysis_results:
            if isinstance(analysis_results[0], dict):
                scored = [a.get("answer_quality", 0) for a in analysis_results if a.get("answer_quality", 0) > 0]
            else:
                scored = [a.answer_quality for a in analysis_results if a.answer_quality > 0]
            if scored:
                avg_quality = round(sum(scored) / len(scored))

        return GeneratedReport(
            session_id=input_data.session_id,
            recommendation=Recommendation.LEAN_HIRE,
            confidence_level=0.3,
            scores=[
                CriterionScoreSchema(criterion="problem_solving", score=avg_quality, evidence=[], trend="stable"),
                CriterionScoreSchema(criterion="code_quality", score=avg_quality, evidence=[], trend="stable"),
                CriterionScoreSchema(criterion="communication", score=avg_quality, evidence=[], trend="stable"),
                CriterionScoreSchema(criterion="hint_responsiveness", score=3, evidence=[], trend="stable"),
                CriterionScoreSchema(criterion="system_design", score=3, evidence=[], trend="stable"),
                CriterionScoreSchema(criterion="resume_verification", score=3, evidence=[], trend="stable"),
                CriterionScoreSchema(criterion="behavioral", score=3, evidence=[], trend="stable"),
            ],
            strengths=["Report generated with limited data"],
            areas_for_growth=["Full LLM analysis was unavailable"],
            level_comparison="Unable to compare due to limited analysis",
        )

    def _compute_hint_summary(self, input_data: ReportInput) -> dict:
        """Compute the hint interaction summary for the report."""
        hint_events = input_data.hint_events
        metrics = input_data.hint_metrics

        hint_details = []
        for h in hint_events:
            if isinstance(h, dict):
                hint_details.append(
                    {
                        "question_id": h.get("question_id", ""),
                        "level": h.get("level", 0),
                        "interviewer_used": h.get("interviewer_used", False),
                        "response_quality": h.get("candidate_response_quality", "none"),
                        "time_to_respond_ms": h.get("time_to_respond_ms"),
                    }
                )
            else:
                hint_details.append(
                    {
                        "question_id": h.question_id,
                        "level": h.level,
                        "interviewer_used": h.interviewer_used,
                        "response_quality": h.candidate_response_quality.value
                        if hasattr(h.candidate_response_quality, "value")
                        else str(h.candidate_response_quality),
                        "time_to_respond_ms": h.time_to_respond_ms,
                    }
                )

        if isinstance(metrics, dict):
            return {
                "total_hints_given": metrics.get("total_hints_given", 0),
                "incorporation_rate": metrics.get("incorporation_rate", 0),
                "average_response_time_ms": metrics.get("average_response_time_ms"),
                "independence_trajectory": metrics.get("independence_trajectory", "unknown"),
                "level_distribution": metrics.get("level_distribution", {}),
                "details": hint_details,
            }

        return {
            "total_hints_given": metrics.total_hints_given,
            "incorporation_rate": metrics.incorporation_rate,
            "average_response_time_ms": metrics.average_response_time_ms,
            "independence_trajectory": metrics.independence_trajectory,
            "level_distribution": metrics.level_distribution,
            "details": hint_details,
        }

    def _build_annotated_transcript(self, input_data: ReportInput) -> list[dict]:
        """Build annotated transcript merging transcripts with events.

        Merges full transcript with analysis events, hint events, and
        contradiction events at matching timestamps.
        """
        if not input_data.full_transcript:
            return []

        # Build event index by timestamp for fast lookup
        analysis_by_ts: dict[str, list[dict]] = {}
        if input_data.all_analysis_events:
            for ae in input_data.all_analysis_events:
                ts = ae.get("timestamp", ae.get("created_at", ""))
                if ts:
                    analysis_by_ts.setdefault(ts, []).append(ae)

        hint_by_ts: dict[str, list[dict]] = {}
        hint_events = input_data.hint_events_db or []
        for h in hint_events:
            ts = h.get("displayed_at", "")
            if ts:
                hint_by_ts.setdefault(ts, []).append(h)

        contradiction_by_ts: dict[str, list[dict]] = {}
        contradictions = input_data.contradictions or []
        for c in contradictions:
            if isinstance(c, dict):
                ts = c.get("timestamp", "")
                if ts:
                    contradiction_by_ts.setdefault(ts, []).append(c)

        code_milestone_by_ts: dict[str, list[dict]] = {}
        if input_data.code_snapshots:
            for cs in input_data.code_snapshots:
                ts = cs.get("timestamp", "")
                if ts:
                    code_milestone_by_ts.setdefault(ts, []).append(cs)

        annotated = []
        for t in input_data.full_transcript:
            ts = t.get("timestamp", "")
            entry: dict = {
                "timestamp": ts,
                "speaker": t.get("speaker_role", "unknown"),
                "text": t.get("text", ""),
                "annotations": [],
            }

            # Match analysis events near this timestamp
            for a_ts, events in analysis_by_ts.items():
                if _timestamps_close(ts, a_ts, tolerance_sec=5):
                    for ae in events:
                        red_flags = ae.get("red_flags") or []
                        if red_flags:
                            for flag in red_flags:
                                entry["annotations"].append({"type": "red_flag", "text": flag})
                        insight = ae.get("insight", "")
                        if insight:
                            entry["annotations"].append({"type": "analysis", "text": insight})

            # Match hint events
            for h_ts, events in hint_by_ts.items():
                if _timestamps_close(ts, h_ts, tolerance_sec=5):
                    for he in events:
                        entry["annotations"].append(
                            {
                                "type": "hint",
                                "text": f"Level {he.get('level', '?')} hint: {he.get('text', '')[:100]}",
                            }
                        )

            # Match contradictions
            for c_ts, events in contradiction_by_ts.items():
                if _timestamps_close(ts, c_ts, tolerance_sec=10):
                    for ce in events:
                        entry["annotations"].append(
                            {
                                "type": "contradiction",
                                "text": ce.get("explanation", ""),
                            }
                        )

            # Match code milestones
            for cm_ts, events in code_milestone_by_ts.items():
                if _timestamps_close(ts, cm_ts, tolerance_sec=10):
                    for cs in events:
                        entry["annotations"].append(
                            {
                                "type": "code_milestone",
                                "text": f"Code: {cs.get('progress_state', 'unknown')}",
                            }
                        )

            annotated.append(entry)

        return annotated

    def compute_hint_score(self, metrics: HintResponseMetricsSchema) -> int:
        """Compute the hint responsiveness score (1-5) from metrics."""
        if metrics.total_hints_given == 0:
            return 3

        score = 3.0

        score += metrics.incorporation_rate * 1.5

        if metrics.average_response_time_ms is not None:
            if metrics.average_response_time_ms < 30000:
                score += 0.5
            elif metrics.average_response_time_ms > 120000:
                score -= 0.5

        if metrics.independence_trajectory == "improving":
            score += 0.5
        elif metrics.independence_trajectory == "declining":
            score -= 0.5

        level_3_ratio = metrics.level_distribution.get(3, 0) / max(1, metrics.total_hints_given)
        if level_3_ratio > 0.5:
            score -= 0.5

        return min(5, max(1, round(score)))


def _timestamps_close(ts1: str, ts2: str, tolerance_sec: int = 5) -> bool:
    """Check if two timestamp strings are within tolerance seconds of each other.

    Supports ISO format and HH:MM:SS format.
    """
    try:
        t1 = _parse_ts(ts1)
        t2 = _parse_ts(ts2)
        if t1 is not None and t2 is not None:
            return abs((t1 - t2).total_seconds()) <= tolerance_sec
    except Exception:
        logger.debug("timestamp_parse_failed", ts1=ts1, ts2=ts2, exc_info=True)
    # Fallback: exact string match
    return ts1 == ts2


def _parse_ts(ts: str) -> datetime | None:
    """Parse a timestamp string to datetime."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        pass
    # Try HH:MM:SS
    try:
        parts = ts.split(":")
        if len(parts) == 3:
            return datetime(2000, 1, 1, int(parts[0]), int(parts[1]), int(float(parts[2])))
    except (ValueError, IndexError):
        pass
    return None
