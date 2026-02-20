"""
Session management API routes.

Endpoints for starting, pausing, resuming, and ending interview sessions.
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter

from src.schemas.session import (
    CreateSessionRequest,
    SessionResponse,
    SessionStatus,
)
from src.schemas.hint import TriggerHintRequest, AcknowledgeHintRequest

router = APIRouter(prefix="/sessions", tags=["sessions"])


# In-memory session store (replaced by DB in production)
_sessions: dict[str, dict] = {}


@router.post("/start")
async def start_session(req: CreateSessionRequest) -> dict:
    session_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    _sessions[session_id] = {
        "id": session_id,
        "user_id": "user-placeholder",
        "status": SessionStatus.LIVE,
        "level": req.level,
        "duration_minutes": req.duration_minutes,
        "focus_areas": req.focus_areas,
        "job_description": req.job_description,
        "started_at": now,
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
    # Placeholder — in production this calls hint_service
    return {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "question_id": req.question_id,
        "level": 1,
        "text": "Consider the time complexity of your approach.",
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
