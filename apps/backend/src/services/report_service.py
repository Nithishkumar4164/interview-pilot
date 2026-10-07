"""Report service — orchestrates report generation and persistence.

Collects data from all session services and delegates to ReportGenerator.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.llm.failover import FailoverManager
from src.services.report_generator import GeneratedReport, ReportGenerator, ReportInput
from src.utils.logging import get_logger

logger = get_logger("report_service")


def _coerce_datetime(value: str | datetime | None) -> datetime:
    """Best-effort conversion of a transcript-timestamp value to a datetime.

    Accepts a ``datetime`` (returned as-is when tz-aware), an ISO-8601
    string (with or without trailing ``Z``), or ``None`` — the caller
    handles the resulting ``TypeError`` for ``None``.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if value is None:
        raise TypeError("timestamp is None")
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    return datetime.fromisoformat(text)


class ReportService:
    """Orchestrates report generation from session data.

    Usage:
        service = ReportService(failover_manager)
        report_input = await service.load_inputs(session_id, snapshots, db)
    """

    def __init__(self, failover_manager: FailoverManager) -> None:
        self._generator = ReportGenerator(failover_manager)

    async def generate_report_from_db(
        self,
        session_id: str,
        snapshots: dict,
        db: AsyncSession,
    ) -> GeneratedReport:
        """Generate a report using DB data + captured snapshots.

        NOTE: holds ``db`` open across the LLM call. Prefer
        :meth:`load_inputs` + :meth:`generate_from_inputs` when you need to
        release the DB connection during the LLM request.
        """
        report_input = await self.load_inputs(session_id, snapshots, db)
        return await self.generate_from_inputs(report_input)

    async def generate_from_inputs(self, report_input: ReportInput) -> GeneratedReport:
        """Run the LLM call. No DB access — safe to invoke without a session."""
        report = await self._generator.generate(report_input)

        logger.info(
            "report_generated_from_db",
            session_id=report_input.session_id,
            recommendation=report.recommendation.value,
            confidence=report.confidence_level,
        )

        return report

    async def load_inputs(
        self,
        session_id: str,
        snapshots: dict,
        db: AsyncSession,
    ) -> ReportInput:
        """Load all DB data needed for report generation into a ReportInput.

        Callers should close their DB session after this returns and before
        invoking :meth:`generate_from_inputs` so the LLM call does not hold
        a connection.
        """
        from src.repositories.analysis_repo import AnalysisEventRepository
        from src.repositories.code_snapshot_repo import CodeSnapshotRepository
        from src.repositories.hint_repo import HintEventRepository
        from src.repositories.transcript_repo import TranscriptRepository

        sid = uuid.UUID(session_id)

        # Query all DB data
        transcript_repo = TranscriptRepository(db)
        transcripts = await transcript_repo.get_session_transcripts(sid, final_only=True, limit=500)

        analysis_repo = AnalysisEventRepository(db)
        analysis_events = await analysis_repo.get_session_events(sid, limit=200)

        code_repo = CodeSnapshotRepository(db)
        code_snapshots_db = await code_repo.get_session_snapshots(sid, limit=100)

        hint_repo = HintEventRepository(db)
        hint_events_db = await hint_repo.get_session_hints(sid)

        # Fetch plan data
        from sqlalchemy import select

        from src.models.plan import InterviewPlan

        plan_result = await db.execute(
            select(InterviewPlan)
            .where(InterviewPlan.session_id == sid)
            .where(InterviewPlan.is_active == True)  # noqa: E712
            .order_by(InterviewPlan.version.desc())
            .limit(1)
        )
        plan = plan_result.scalar_one_or_none()

        # Build full transcript list
        full_transcript = [
            {
                "speaker_role": t.speaker_role,
                "text": t.text,
                "timestamp": (t.timestamp.isoformat() if isinstance(t.timestamp, datetime) else t.timestamp),
                "is_final": t.is_final,
            }
            for t in transcripts
        ]

        # Build analysis events list
        all_analysis = [
            {
                "answer_quality": ae.answer_quality,
                "on_track": ae.on_track,
                "insight": ae.insight,
                "red_flags": ae.red_flags or [],
                "suggestion": ae.suggestion,
                "hint_signal": ae.hint_signal,
                "timestamp": ae.created_at.isoformat() if ae.created_at else "",
            }
            for ae in analysis_events
        ]

        # Build code snapshots list
        code_snaps = [
            {
                "timestamp": (cs.timestamp.isoformat() if isinstance(cs.timestamp, datetime) else cs.timestamp),
                "progress_state": cs.progress_state,
                "approach": cs.approach,
                "language": cs.language,
            }
            for cs in code_snapshots_db
        ]

        # Build hint events list from DB
        hint_events_list = [
            {
                "question_id": h.question_id,
                "level": h.level,
                "text": h.text,
                "displayed_at": (
                    h.displayed_at.isoformat() if isinstance(h.displayed_at, datetime) else h.displayed_at
                ),
                "interviewer_used": h.interviewer_used,
                "candidate_response_quality": h.candidate_response_quality,
                "time_to_respond_ms": h.time_to_respond_ms,
            }
            for h in hint_events_db
        ]

        # Derive time-per-question from transcript timestamps
        time_per_question = self._derive_time_per_question(transcripts, plan)

        # Build ReportInput using both snapshots and DB data
        fact_store = snapshots.get("fact_store", "")
        rolling_summary = snapshots.get("rolling_summary", "")
        snapshot_analysis = snapshots.get("analysis_results", [])
        snapshot_contradictions = snapshots.get("contradictions", [])
        snapshot_hints = snapshots.get("hint_events", [])
        hint_metrics = snapshots.get(
            "hint_metrics",
            {
                "total_hints_given": len(hint_events_list),
                "incorporation_rate": 0,
                "average_response_time_ms": None,
                "independence_trajectory": "unknown",
                "level_distribution": {},
            },
        )

        report_input = ReportInput(
            session_id=session_id,
            duration_minutes=snapshots.get("duration_minutes", 0),
            target_level=snapshots.get("target_level", "sde2"),
            fact_store=fact_store,
            analysis_results=snapshot_analysis or all_analysis,
            hint_events=snapshot_hints or hint_events_list,
            hint_metrics=hint_metrics,
            contradictions=snapshot_contradictions,
            transcript_summary=rolling_summary,
            code_progress=snapshots.get("code_progress", "unknown"),
            detected_approach=snapshots.get("detected_approach"),
            # Enhanced fields
            full_transcript=full_transcript,
            all_analysis_events=all_analysis,
            code_snapshots=code_snaps,
            hint_events_db=hint_events_list,
            time_per_question=time_per_question,
            plan_data=plan.plan_data if plan else None,
        )

        logger.info(
            "report_inputs_loaded",
            session_id=session_id,
            transcript_count=len(full_transcript),
            analysis_count=len(all_analysis),
        )

        return report_input

    @staticmethod
    def _derive_time_per_question(transcripts, plan) -> dict[str, float]:
        """Derive time spent per question from transcript timestamps and plan data."""
        if not plan or not transcripts:
            return {}

        plan_data = plan.plan_data if hasattr(plan, "plan_data") else {}
        phases = plan_data.get("phases", [])
        question_ids = []
        for phase in phases:
            for q in phase.get("questions", []):
                qid = q.get("id", "")
                if qid:
                    question_ids.append(qid)

        if not question_ids:
            return {}

        # Simple heuristic: divide total time evenly across questions
        # since we can't reliably map transcripts to specific questions
        total_transcripts = len(transcripts)
        if total_transcripts == 0:
            return {}

        try:
            first_ts = transcripts[0].timestamp
            last_ts = transcripts[-1].timestamp

            # ``timestamp`` is stored as timestamptz now, but historic
            # rows (and tests) may still carry ISO strings — accept both.
            first_dt = _coerce_datetime(first_ts)
            last_dt = _coerce_datetime(last_ts)
            total_minutes = (last_dt - first_dt).total_seconds() / 60.0
        except (ValueError, AttributeError, TypeError):
            total_minutes = 0

        if total_minutes <= 0:
            return {}

        per_question = total_minutes / len(question_ids)
        return {qid: round(per_question, 1) for qid in question_ids}
