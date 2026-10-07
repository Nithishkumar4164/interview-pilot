"""
Session management API routes.

Endpoints for starting, pausing, resuming, and ending interview sessions.
Also handles hint registration from plans and hint progression during interviews.
"""

import contextlib
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.authz import authorized_session, get_user_principal
from src.config import settings
from src.dependencies import get_db
from src.models.session import InterviewSession
from src.repositories.session_repo import SessionRepository
from src.schemas.auth import PairingTokenResponse
from src.schemas.hint import AcknowledgeHintRequest, TriggerHintRequest
from src.schemas.session import (
    CreateSessionRequest,
    SessionStatus,
)
from src.services.access_control import Principal
from src.services.hint_service import HintService
from src.utils.logging import get_logger

logger = get_logger("api.sessions")

router = APIRouter(prefix="/sessions", tags=["sessions"])

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
    principal: Principal = Depends(get_user_principal),
) -> dict:
    session_repo = SessionRepository(db)

    db_session = InterviewSession(
        user_id=principal.user_id,
        status=SessionStatus.PLANNING,
        level=req.level,
        duration_minutes=req.duration_minutes,
        focus_areas=req.focus_areas,
        job_description=req.job_description,
        custom_notes=req.custom_notes,
        candidate_name=req.candidate_name,
        candidate_email=req.candidate_email,
        resume_data=req.resume_data,
    )
    await session_repo.create(db_session)
    await db.commit()

    return {"session_id": str(db_session.id)}


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    db_session: InterviewSession = Depends(authorized_session),
) -> dict:

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
    db_session: InterviewSession = Depends(authorized_session),
) -> dict:
    """Get full session state for recovery after page reload.

    Returns session metadata, recent transcripts, analyses, hints,
    and plan data for restoring the frontend.
    """

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
    transcripts = await transcript_repo.get_session_transcripts(uuid.UUID(session_id), final_only=True, limit=200)

    # Fetch recent analyses from DB
    from src.repositories.analysis_repo import AnalysisEventRepository

    analysis_repo = AnalysisEventRepository(db)
    analyses = await analysis_repo.get_recent(uuid.UUID(session_id), count=50)

    # Fetch hint events from DB
    from src.repositories.hint_repo import HintEventRepository

    hint_repo = HintEventRepository(db)
    hints = await hint_repo.get_session_hints(uuid.UUID(session_id))

    # Fetch plan data

    from src.models.plan import InterviewPlan

    plan_result = await db.execute(
        select(InterviewPlan)
        .where(InterviewPlan.session_id == uuid.UUID(session_id))
        .where(InterviewPlan.is_active == True)  # noqa: E712
        .order_by(InterviewPlan.version.desc())
        .limit(1)
    )
    plan = plan_result.scalar_one_or_none()

    # Get session state snapshot
    snapshot_data = None
    try:
        snapshot_data = await _graph_state_snapshot(session_id, hints)
    except Exception:
        import logging

        logging.getLogger("api.sessions").warning("session_snapshot_recover_failed", exc_info=True)

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
                "root_speaker_id": t.root_speaker_id,
                "speech_vector_id": t.speech_vector_id,
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
                "dimensions": a.dimensions,
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
    db_session: InterviewSession = Depends(authorized_session),
) -> dict:
    """Transition session from PLANNING to LIVE."""
    session_repo = SessionRepository(db)

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
async def register_plan_hints(
    session_id: str,
    req: RegisterPlanHintsRequest,
    _session: InterviewSession = Depends(authorized_session),
) -> dict:
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
    db_session: InterviewSession = Depends(authorized_session),
) -> dict:
    session_repo = SessionRepository(db)

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
    db_session: InterviewSession = Depends(authorized_session),
) -> dict:
    session_repo = SessionRepository(db)

    now = datetime.now(UTC)
    db_session.status = SessionStatus.ENDED
    db_session.ended_at = now
    await session_repo.update(db_session)

    # Create placeholder report and launch background generation
    report_id = None
    try:
        from src.models.report import Report
        from src.repositories.report_repo import ReportRepository
        from src.websocket.audio_handler import _active_workers

        worker = _active_workers.get(session_id)
        hint_service = _hint_services.get(session_id)

        # Drain in-flight background tasks so snapshots capture complete data
        # Use a hard timeout so the endpoint never hangs
        import asyncio

        if worker:
            with contextlib.suppress(TimeoutError, Exception):
                await asyncio.wait_for(worker.drain(), timeout=5.0)

        # Calculate duration
        duration_minutes: float = db_session.duration_minutes
        if db_session.started_at:
            elapsed = (now - db_session.started_at).total_seconds() / 60.0
            duration_minutes = round(elapsed, 1)

        # Capture snapshots from in-memory services before cleanup
        snapshots: dict = {
            "session_id": session_id,
            "duration_minutes": duration_minutes,
            "target_level": db_session.level,
        }
        stored = None
        if worker:
            snapshots.update(worker.report_snapshot())
        else:
            # This process never held the pipeline (restart, or another worker):
            # the session's state is in its checkpoints.
            from src.dependencies import failover_manager, get_checkpointer
            from src.workers.langgraph_session import load_checkpointed_session

            stored = await load_checkpointed_session(get_checkpointer(), failover_manager, session_id)
            if stored is not None:
                snapshots.update(stored.report_snapshot())
        if hint_service:
            snapshots["hint_events"] = [
                {
                    "question_id": h.question_id,
                    "level": h.level,
                    "interviewer_used": h.interviewer_used,
                    "candidate_response_quality": (
                        h.candidate_response_quality.value
                        if hasattr(h.candidate_response_quality, "value")
                        else str(h.candidate_response_quality)
                    ),
                    "time_to_respond_ms": h.time_to_respond_ms,
                    "hint_id": h.hint_id,
                    "text": h.text,
                }
                for h in hint_service.get_events()
            ]
            snapshots["hint_metrics"] = hint_service.get_metrics().model_dump()
        # Create placeholder report row with status=pending
        report_repo = ReportRepository(db)
        placeholder_report = Report(
            session_id=uuid.UUID(session_id),
            report_data={},
            recommendation="lean_hire",
            confidence_level=0.0,
            is_final=False,
            status="pending",
        )
        await report_repo.create(placeholder_report)
        report_id = str(placeholder_report.id)

        await db.commit()

        # Cleanup in-memory services
        _hint_services.pop(session_id, None)
        from src.websocket.audio_handler import close_transcription_service

        await close_transcription_service(session_id)
        # Whether or not this process held the pipeline, the session is over:
        # its report inputs and final snapshot are saved, so drop its checkpoints.
        from src.dependencies import get_checkpointer
        from src.workers.langgraph_session import delete_session_threads

        await delete_session_threads(get_checkpointer(), session_id)

        # Fire background task for report generation
        asyncio.create_task(
            _generate_report_background(
                report_id=report_id,
                session_id=session_id,
                snapshots=snapshots,
            )
        )

    except Exception as e:
        import logging

        logging.getLogger("api.sessions").error(f"Report setup failed: {e}")
        await db.commit()
        _hint_services.pop(session_id, None)

    result = {"status": "ended", "session_id": session_id}
    if report_id:
        result["report_id"] = report_id
    return result


async def _generate_report_background(
    report_id: str,
    session_id: str,
    snapshots: dict,
) -> None:
    """Generate and persist a report via ReportGraph.

    The graph releases its DB connection before the LLM call, checkpoints each
    step, and is resumed at startup if the process dies mid-run. Failures mark
    the report failed; this never raises.
    """
    from src.dependencies import get_report_runner

    await get_report_runner().run(report_id=report_id, session_id=session_id, snapshots=snapshots)


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    db_session: InterviewSession = Depends(authorized_session),
) -> dict:
    """Delete a session. Only allowed for planning-status sessions."""
    session_repo = SessionRepository(db)

    if db_session.status not in (SessionStatus.PLANNING, SessionStatus.READY):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Can only delete sessions in planning/ready status, currently: {db_session.status}",
        )

    await session_repo.delete(db_session)
    await db.commit()

    return {"status": "deleted", "session_id": session_id}


@router.post("/{session_id}/hints/next")
async def trigger_next_hint(
    session_id: str,
    req: TriggerHintRequest,
    _session: InterviewSession = Depends(authorized_session),
) -> dict:
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

    # Inform the analysis worker so subsequent LLM calls include
    # the hint_reception scoring dimension.
    from src.websocket.audio_handler import _active_workers

    worker = _active_workers.get(session_id)
    if worker:
        worker.set_active_hint(active.text)

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
    _session: InterviewSession = Depends(authorized_session),
) -> dict:
    """Acknowledge a hint with usage and response metrics.

    The hint_id from the frontend may not match the backend-generated ID
    stored in the DB (frontend creates its own UUID). We try an exact-ID
    match first, then fall back to updating the latest unacknowledged hint
    for this session.  If the hint row hasn't been flushed from the
    PersistenceService queue yet, neither query will find it — the in-memory
    HintService state (updated below) is the authoritative source and will
    be captured at session end for the report.
    """
    # 1. Update in-memory state (always succeeds — uses backend hint_id)
    service = _hint_services.get(session_id)
    if service:
        # Try matching by backend hint_id first, then by frontend hint_id
        matched = service.acknowledge_hint(
            hint_id=hint_id,
            interviewer_used=req.interviewer_used,
            candidate_response_quality=req.candidate_response_quality,
        )
        if not matched:
            # Frontend hint_id didn't match — acknowledge the most recent hint
            current = service.get_current_hint()
            if current:
                service.acknowledge_hint(
                    hint_id=current.hint_id,
                    interviewer_used=req.interviewer_used,
                    candidate_response_quality=req.candidate_response_quality,
                )

    # 2. Update DB record
    try:
        from src.repositories.hint_repo import HintEventRepository

        hint_repo = HintEventRepository(db)
        quality_str = (
            req.candidate_response_quality.value
            if hasattr(req.candidate_response_quality, "value")
            else str(req.candidate_response_quality)
        )

        # Try exact ID match first (works if frontend and backend IDs happen to align)
        updated = False
        with contextlib.suppress(ValueError):
            # hint_id may not be a valid UUID — fall through to the
            # latest-unacked lookup below.
            updated = await hint_repo.update_acknowledgment(
                hint_id=uuid.UUID(hint_id),
                session_id=uuid.UUID(session_id),
                interviewer_used=req.interviewer_used,
                candidate_response_quality=quality_str,
            )

        # Fall back: find latest unacknowledged hint for this session
        if not updated:
            await hint_repo.acknowledge_latest_unacked(
                session_id=uuid.UUID(session_id),
                interviewer_used=req.interviewer_used,
                candidate_response_quality=quality_str,
            )
    except Exception:
        # Row may not be flushed yet; in-memory state is authoritative.
        import logging

        logging.getLogger("api.sessions").warning("hint_ack_row_update_failed", exc_info=True)

    return {
        "hint_id": hint_id,
        "acknowledged": True,
        "quality": req.candidate_response_quality,
    }


@router.post("/{session_id}/pairing-token", response_model=PairingTokenResponse)
async def create_pairing_token(
    session_id: str,
    principal: Principal = Depends(get_user_principal),
    _session: InterviewSession = Depends(authorized_session),
    db: AsyncSession = Depends(get_db),
) -> PairingTokenResponse:
    """Mint a credential for the Chrome extension that works only for this session."""
    from src.services.auth_service import AuthService

    return PairingTokenResponse(
        session_id=session_id,
        pairing_token=AuthService(db).create_pairing_token(str(principal.user_id), session_id),
        expires_in=settings.pairing_token_expire_hours * 3600,
    )


async def _graph_state_snapshot(session_id: str, hints: list) -> dict | None:
    """/state snapshot from the session's checkpoints; None once they are gone (ended session)."""
    from src.dependencies import failover_manager, get_checkpointer
    from src.workers.langgraph_session import read_state_snapshot

    hint_history = [
        {"hint_id": str(h.id), "question_id": h.question_id, "level": h.level, "displayed_at": h.displayed_at}
        for h in hints
    ]
    snapshot = await read_state_snapshot(get_checkpointer(), failover_manager, session_id, hint_history)
    return jsonable_encoder(snapshot) if snapshot is not None else None
