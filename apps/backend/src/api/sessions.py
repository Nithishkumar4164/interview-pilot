"""
Session management API routes.

Endpoints for starting, pausing, resuming, and ending interview sessions.
Also handles hint registration from plans and hint progression during interviews.
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from src.schemas.session import (
    CreateSessionRequest,
    SessionResponse,
    SessionStatus,
)
from src.schemas.hint import TriggerHintRequest, AcknowledgeHintRequest
from src.services.hint_service import HintService

router = APIRouter(prefix="/sessions", tags=["sessions"])


# In-memory session store (replaced by DB in production)
_sessions: dict[str, dict] = {}

# Per-session HintService instances
_hint_services: dict[str, HintService] = {}


def _get_or_create_hint_service(session_id: str) -> HintService:
    if session_id not in _hint_services:
        _hint_services[session_id] = HintService(session_id)
    return _hint_services[session_id]


class RegisterPlanHintsRequest(BaseModel):
    phases: list[dict]


@router.post("/start")
async def start_session(req: CreateSessionRequest) -> dict:
    session_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    _sessions[session_id] = {
        "id": session_id,
        "user_id": "user-placeholder",
        "status": SessionStatus.PLANNING,
        "level": req.level,
        "duration_minutes": req.duration_minutes,
        "focus_areas": req.focus_areas,
        "job_description": req.job_description,
        "resume_reference_id": req.resume_reference_id,
        "plan_id": None,
        "started_at": None,
        "ended_at": None,
        "created_at": now,
        "updated_at": now,
    }
    return {"session_id": session_id}


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict:
    session = _sessions.get(session_id)
    if session is None:
        return {"error": "Session not found"}
    return session


@router.post("/{session_id}/go-live")
async def go_live(session_id: str) -> dict:
    """Transition session from PLANNING to LIVE."""
    session = _sessions.get(session_id)
    if session is None:
        return {"error": "Session not found"}

    if session["status"] not in (SessionStatus.PLANNING, SessionStatus.READY):
        return {"error": f"Session must be in PLANNING or READY status, currently: {session['status']}"}

    now = datetime.now(UTC).isoformat()
    session["status"] = SessionStatus.LIVE
    session["started_at"] = now
    session["updated_at"] = now
    return {"status": "live", "session_id": session_id, "started_at": now}


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

    # Store plan_id link on session
    session = _sessions.get(session_id)
    if session:
        session["updated_at"] = datetime.now(UTC).isoformat()

    return {"registered_questions": count}


@router.patch("/{session_id}/pause")
async def pause_session(session_id: str) -> dict:
    session = _sessions.get(session_id)
    if session is None:
        return {"error": "Session not found"}

    if session["status"] == SessionStatus.LIVE:
        session["status"] = SessionStatus.PAUSED
    elif session["status"] == SessionStatus.PAUSED:
        session["status"] = SessionStatus.LIVE

    session["updated_at"] = datetime.now(UTC).isoformat()
    return {"status": session["status"]}


@router.post("/{session_id}/end")
async def end_session(session_id: str) -> dict:
    session = _sessions.get(session_id)
    if session is None:
        return {"error": "Session not found"}

    session["status"] = SessionStatus.ENDED
    session["ended_at"] = datetime.now(UTC).isoformat()
    session["updated_at"] = session["ended_at"]
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
