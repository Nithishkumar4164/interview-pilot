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
    await db.commit()

    return {"status": "ended", "session_id": session_id}


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
) -> dict:
    return {
        "hint_id": hint_id,
        "acknowledged": True,
        "quality": req.candidate_response_quality,
    }
