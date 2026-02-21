"""Report service — orchestrates report generation and persistence.

Collects data from all session services and delegates to ReportGenerator.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.llm.failover import FailoverManager
from src.services.analysis_service import AnalysisService
from src.services.code_analysis_service import CodeAnalysisService
from src.services.contradiction_service import ContradictionService
from src.services.hint_service import HintService
from src.services.memory_service import MemoryService
from src.services.report_generator import GeneratedReport, ReportGenerator, ReportInput
from src.utils.logging import get_logger

logger = get_logger("report_service")


class ReportService:
    """Orchestrates report generation from session data.

    Usage:
        service = ReportService(failover_manager)
        report = await service.generate_report(...)
        report = await service.generate_report_from_db(...)
    """

    def __init__(self, failover_manager: FailoverManager) -> None:
        self._generator = ReportGenerator(failover_manager)

    async def generate_report(
        self,
        session_id: str,
        memory: MemoryService,
        analysis: AnalysisService,
        code_analysis: CodeAnalysisService | None,
        hints: HintService,
        contradictions: ContradictionService,
        duration_minutes: float,
        target_level: str,
    ) -> GeneratedReport:
        """Generate a comprehensive interview report from live services."""
        code_progress = "unknown"
        detected_approach = None
        if code_analysis:
            result = code_analysis.get_latest_result()
            if result:
                code_progress = result.progress_state.value
                if result.detected_approach:
                    detected_approach = result.detected_approach.name

        report_input = ReportInput(
            session_id=session_id,
            duration_minutes=duration_minutes,
            target_level=target_level,
            fact_store=memory.fact_store,
            analysis_results=analysis.get_recent_analyses(count=50),
            hint_events=hints._hint_events,
            hint_metrics=hints.get_metrics(),
            contradictions=contradictions.contradictions,
            transcript_summary=memory._rolling_summary,
            code_progress=code_progress,
            detected_approach=detected_approach,
        )

        report = await self._generator.generate(report_input)

        logger.info(
            "report_generated",
            session_id=session_id,
            recommendation=report.recommendation.value,
            confidence=report.confidence_level,
        )

        return report

    async def generate_report_from_db(
        self,
        session_id: str,
        snapshots: dict,
        db: AsyncSession,
    ) -> GeneratedReport:
        """Generate a report using DB data + captured snapshots.

        This is called from the background task after session end.
        Queries DB for transcripts, analysis events, code snapshots, hints,
        and plan data to build a comprehensive report.
        """
        from src.repositories.transcript_repo import TranscriptRepository
        from src.repositories.analysis_repo import AnalysisEventRepository
        from src.repositories.code_snapshot_repo import CodeSnapshotRepository
        from src.repositories.hint_repo import HintEventRepository

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
                "timestamp": t.timestamp,
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
                "timestamp": cs.timestamp,
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
                "displayed_at": h.displayed_at,
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
        hint_metrics = snapshots.get("hint_metrics", {
            "total_hints_given": len(hint_events_list),
            "incorporation_rate": 0,
            "average_response_time_ms": None,
            "independence_trajectory": "unknown",
            "level_distribution": {},
        })

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

        report = await self._generator.generate(report_input)

        logger.info(
            "report_generated_from_db",
            session_id=session_id,
            recommendation=report.recommendation.value,
            confidence=report.confidence_level,
            transcript_count=len(full_transcript),
            analysis_count=len(all_analysis),
        )

        return report

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

        from datetime import datetime

        try:
            first_ts = transcripts[0].timestamp
            last_ts = transcripts[-1].timestamp

            first_dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
            last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            total_minutes = (last_dt - first_dt).total_seconds() / 60.0
        except (ValueError, AttributeError):
            total_minutes = 0

        if total_minutes <= 0:
            return {}

        per_question = total_minutes / len(question_ids)
        return {qid: round(per_question, 1) for qid in question_ids}
