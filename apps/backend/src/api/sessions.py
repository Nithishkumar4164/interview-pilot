"""
Session management API routes.

Endpoints for starting, pausing, resuming, and ending interview sessions.
Also handles hint registration from plans and hint progression during interviews.
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.models.session import InterviewSession
from src.repositories.session_repo import SessionRepository
from src.schemas.session import (
    CreateSessionRequest,
    SessionResponse,
    SessionStatus,
)
from src.schemas.hint import TriggerHintRequest, AcknowledgeHintRequest
from src.services.hint_service import HintService

router = APIRouter(prefix="/sessions", tags=["sessions"])

# Default dev user UUID — matches the seeded row in the users table.
_DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

# Per-session HintService instances (kept in-memory; stateless across restarts)
_hint_services: dict[str, HintService] = {}


def _get_or_create_hint_service(session_id: str) -> HintService:
    if session_id not in _hint_services:
        _hint_services[session_id] = HintService(session_id)
    return _hint_services[session_id]


class RegisterPlanHintsRequest(BaseModel):
    phases: list[dict]


@router.post("/start")
async def start_session(
    req: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    session_repo = SessionRepository(db)

    db_session = InterviewSession(
        user_id=_DEV_USER_ID,
        status=SessionStatus.PLANNING,
        level=req.level,
        duration_minutes=req.duration_minutes,
        focus_areas=req.focus_areas,
        job_description=req.job_description,
        custom_notes=req.custom_notes,
    )
    await session_repo.create(db_session)
    await db.commit()

    return {"session_id": str(db_session.id)}


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    session_repo = SessionRepository(db)
    db_session = await session_repo.get_by_id(uuid.UUID(session_id))

    if db_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return {
        "id": str(db_session.id),
        "user_id": str(db_session.user_id),
        "status": db_session.status,
        "level": db_session.level,
        "duration_minutes": db_session.duration_minutes,
        "focus_areas": db_session.focus_areas,
        "job_description": db_session.job_description,
        "plan_id": None,
        "started_at": db_session.started_at.isoformat() if db_session.started_at else None,
        "ended_at": db_session.ended_at.isoformat() if db_session.ended_at else None,
        "created_at": db_session.created_at.isoformat() if db_session.created_at else "",
        "updated_at": db_session.updated_at.isoformat() if db_session.updated_at else "",
    }


@router.get("/{session_id}/state")
async def get_session_state(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get full session state for recovery after page reload.

    Returns session metadata, recent transcripts, analyses, hints,
    and plan data for restoring the frontend.
    """
    session_repo = SessionRepository(db)
    db_session = await session_repo.get_by_id(uuid.UUID(session_id))

    if db_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Compute elapsed time
    elapsed_ms = 0
    if db_session.started_at:
        if db_session.ended_at:
            elapsed_ms = int((db_session.ended_at - db_session.started_at).total_seconds() * 1000)
        else:
            elapsed_ms = int((datetime.now(UTC) - db_session.started_at).total_seconds() * 1000)

    # Fetch recent transcripts from DB
    from src.repositories.transcript_repo import TranscriptRepository
    transcript_repo = TranscriptRepository(db)
    transcripts = await transcript_repo.get_session_transcripts(
        uuid.UUID(session_id), final_only=True, limit=200
    )

    # Fetch recent analyses from DB
    from src.repositories.analysis_repo import AnalysisEventRepository
    analysis_repo = AnalysisEventRepository(db)
    analyses = await analysis_repo.get_recent(uuid.UUID(session_id), count=50)

    # Fetch hint events from DB
    from src.repositories.hint_repo import HintEventRepository
    hint_repo = HintEventRepository(db)
    hints = await hint_repo.get_session_hints(uuid.UUID(session_id))

    # Fetch plan data
    from sqlalchemy import select
    from src.models.plan import InterviewPlan
    plan_result = await db.execute(
        select(InterviewPlan)
        .where(InterviewPlan.session_id == uuid.UUID(session_id))
        .where(InterviewPlan.is_active == True)  # noqa: E712
        .order_by(InterviewPlan.version.desc())
        .limit(1)
    )
    plan = plan_result.scalar_one_or_none()

    # Get session state snapshot (in-memory or from DB)
    snapshot_data = None
    try:
        from src.dependencies import get_session_state_service
        sss = get_session_state_service()
        state = sss._states.get(session_id)
        if state:
            snapshot_data = state.to_snapshot()
        else:
            recovered = await sss.recover_session(session_id, db)
            if recovered:
                snapshot_data = recovered.to_snapshot()
    except Exception:
        pass

    return {
        "session": {
            "id": str(db_session.id),
            "status": db_session.status,
            "level": db_session.level,
            "duration_minutes": db_session.duration_minutes,
            "elapsed_ms": elapsed_ms,
            "started_at": db_session.started_at.isoformat() if db_session.started_at else None,
            "ended_at": db_session.ended_at.isoformat() if db_session.ended_at else None,
        },
        "transcripts": [
            {
                "id": str(t.id),
                "speaker_role": t.speaker_role,
                "text": t.text,
                "is_final": t.is_final,
                "confidence": t.confidence,
                "timestamp": t.timestamp,
                "channel": t.channel,
                "is_retroactive": t.is_retroactive,
            }
            for t in transcripts
        ],
        "analyses": [
            {
                "answer_quality": a.answer_quality,
                "on_track": a.on_track,
                "insight": a.insight,
                "red_flags": a.red_flags or [],
                "suggestion": a.suggestion,
                "hint_signal": a.hint_signal,
                "timestamp": a.created_at.isoformat() if a.created_at else "",
            }
            for a in analyses
        ],
        "hints": [
            {
                "id": str(h.id),
                "question_id": h.question_id,
                "level": h.level,
                "text": h.text,
                "displayed_at": h.displayed_at,
                "interviewer_used": h.interviewer_used,
                "candidate_response_quality": h.candidate_response_quality,
            }
            for h in hints
        ],
        "plan": plan.plan_data if plan else None,
        "plan_id": str(plan.id) if plan else None,
        "snapshot": snapshot_data,
    }


@router.post("/{session_id}/go-live")
async def go_live(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Transition session from PLANNING to LIVE."""
    session_repo = SessionRepository(db)
    db_session = await session_repo.get_by_id(uuid.UUID(session_id))

    if db_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if db_session.status not in (SessionStatus.PLANNING, SessionStatus.READY):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Session must be in PLANNING or READY status, currently: {db_session.status}",
        )

    now = datetime.now(UTC)
    db_session.status = SessionStatus.LIVE
    db_session.started_at = now
    await session_repo.update(db_session)
    await db.commit()

    return {"status": "live", "session_id": session_id, "started_at": now.isoformat()}


@router.post("/{session_id}/register-plan-hints")
async def register_plan_hints(session_id: str, req: RegisterPlanHintsRequest) -> dict:
    """Register all pre-computed hints from a plan into the session's HintService."""
    service = _get_or_create_hint_service(session_id)
    count = 0
    first_question_id = None

    for phase in req.phases:
        for question in phase.get("questions", []):
            q_id = question.get("id", "")
            hints_list = question.get("hints", [])
            hint_map = {h["level"]: h["text"] for h in hints_list if "level" in h and "text" in h}
            if hint_map:
                service.register_hints(q_id, hint_map)
                count += 1
                if first_question_id is None:
                    first_question_id = q_id

    # Activate the first question by default
    if first_question_id:
        service.activate_question(first_question_id)

    return {"registered_questions": count}


@router.patch("/{session_id}/pause")
async def pause_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    session_repo = SessionRepository(db)
    db_session = await session_repo.get_by_id(uuid.UUID(session_id))

    if db_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if db_session.status == SessionStatus.LIVE:
        db_session.status = SessionStatus.PAUSED
    elif db_session.status == SessionStatus.PAUSED:
        db_session.status = SessionStatus.LIVE

    await session_repo.update(db_session)
    await db.commit()

    return {"status": db_session.status}


@router.post("/{session_id}/end")
async def end_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    session_repo = SessionRepository(db)
    db_session = await session_repo.get_by_id(uuid.UUID(session_id))

    if db_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    now = datetime.now(UTC)
    db_session.status = SessionStatus.ENDED
    db_session.ended_at = now
    await session_repo.update(db_session)

    # Generate report from in-memory worker data
    report_id = None
    try:
        from src.websocket.audio_handler import _active_workers
        from src.websocket.code_handler import _active_analyzers
        from src.dependencies import failover_manager, get_session_state_service
        from src.services.report_service import ReportService
        from src.models.report import Report
        from src.repositories.report_repo import ReportRepository

        worker = _active_workers.get(session_id)
        hint_service = _hint_services.get(session_id)
        code_analyzer = _active_analyzers.get(session_id)

        if worker and hint_service:
            # Calculate duration
            duration_minutes = db_session.duration_minutes
            if db_session.started_at:
                elapsed = (now - db_session.started_at).total_seconds() / 60.0
                duration_minutes = round(elapsed, 1)

            report_service = ReportService(failover_manager)
            generated = await report_service.generate_report(
                session_id=session_id,
                memory=worker.memory,
                analysis=worker.analysis,
                code_analysis=code_analyzer,
                hints=hint_service,
                contradictions=worker.contradictions,
                duration_minutes=duration_minutes,
                target_level=db_session.level,
            )

            # Persist report to DB
            report_repo = ReportRepository(db)
            report = Report(
                id=uuid.UUID(generated.id),
                session_id=uuid.UUID(session_id),
                report_data={
                    "scores": [s.model_dump() for s in generated.scores],
                    "key_moments": [m.model_dump() for m in generated.key_moments],
                    "strengths": generated.strengths,
                    "areas_for_growth": generated.areas_for_growth,
                    "level_comparison": generated.level_comparison,
                    "hint_summary": generated.hint_summary,
                    "generated_at": generated.generated_at,
                },
                recommendation=generated.recommendation.value,
                confidence_level=generated.confidence_level,
                is_final=True,
            )
            await report_repo.create(report)
            report_id = str(report.id)

        # Save final session snapshot
        try:
            sss = get_session_state_service()
            await sss.save_snapshot(session_id, db)
            sss.remove_state(session_id)
        except Exception:
            pass

    except Exception as e:
        # Report generation failure should not block session end
        import logging
        logging.getLogger("api.sessions").error(f"Report generation failed: {e}")

    await db.commit()

    # Cleanup in-memory services
    _hint_services.pop(session_id, None)

    result = {"status": "ended", "session_id": session_id}
    if report_id:
        result["report_id"] = report_id
    return result


@router.post("/{session_id}/hints/next")
async def trigger_next_hint(session_id: str, req: TriggerHintRequest) -> dict:
    """Get the next progressive hint for a question."""
    service = _get_or_create_hint_service(session_id)

    # Only activate if switching to a different question
    if req.question_id and service._active_question_id != req.question_id:
        service.activate_question(req.question_id)

    active = service.advance_hint(req.question_id if req.question_id else None)

    if active is None:
        return {
            "id": None,
            "session_id": session_id,
            "question_id": req.question_id,
            "level": 0,
            "text": "No more hints available for this question.",
        }

    return {
        "id": active.hint_id,
        "session_id": session_id,
        "question_id": active.question_id,
        "level": active.level,
        "text": active.text,
    }


@router.post("/{session_id}/hints/{hint_id}/ack")
async def acknowledge_hint(
    session_id: str,
    hint_id: str,
    req: AcknowledgeHintRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Acknowledge a hint with usage and response metrics."""
    # Update in-memory state
    service = _hint_services.get(session_id)
    if service:
        service.acknowledge_hint(
            hint_id=hint_id,
            interviewer_used=req.interviewer_used,
            candidate_response_quality=req.candidate_response_quality,
        )

    # Update DB record
    try:
        from src.repositories.hint_repo import HintEventRepository
        hint_repo = HintEventRepository(db)
        quality_str = req.candidate_response_quality.value if hasattr(req.candidate_response_quality, "value") else str(req.candidate_response_quality)
        await hint_repo.update_acknowledgment(
            hint_id=uuid.UUID(hint_id),
            interviewer_used=req.interviewer_used,
            candidate_response_quality=quality_str,
        )
    except (ValueError, Exception):
        pass  # hint_id may not be a valid UUID or may not exist in DB yet

    return {
        "hint_id": hint_id,
        "acknowledged": True,
        "quality": req.candidate_response_quality,
    }
