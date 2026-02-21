"""Session history and report lookup API routes.

Endpoints:
  GET /api/history/sessions           — List sessions with optional status filter
  GET /api/history/sessions/:id/report — Get report for a completed session
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.repositories.report_repo import ReportRepository
from src.repositories.session_repo import SessionRepository
from src.utils.logging import get_logger

logger = get_logger("api.history")

router = APIRouter(prefix="/history", tags=["history"])

# Default dev user UUID
_DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@router.get("/sessions")
async def list_sessions(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List sessions for the current user with optional status filter."""
    session_repo = SessionRepository(db)
    sessions = await session_repo.get_sessions_for_user(_DEV_USER_ID)

    # Apply status filter if provided
    if status_filter:
        sessions = [s for s in sessions if s.status == status_filter]

    # Apply pagination
    total = len(sessions)
    sessions = sessions[offset : offset + limit]

    return {
        "sessions": [
            {
                "id": str(s.id),
                "status": s.status,
                "level": s.level,
                "duration_minutes": s.duration_minutes,
                "focus_areas": s.focus_areas,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else "",
            }
            for s in sessions
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/sessions/{session_id}/report")
async def get_session_report(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get the report for a completed session."""
    report_repo = ReportRepository(db)
    report = await report_repo.get_by_session(uuid.UUID(session_id))

    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No report found for session {session_id}",
        )

    return {
        "id": str(report.id),
        "session_id": str(report.session_id),
        "report_data": report.report_data,
        "recommendation": report.recommendation,
        "confidence_level": report.confidence_level,
        "is_final": report.is_final,
        "created_at": report.created_at.isoformat() if report.created_at else "",
        "notes": [
            {
                "id": str(n.id),
                "note_text": n.note_text,
                "user_id": str(n.user_id),
                "created_at": n.created_at.isoformat() if n.created_at else "",
            }
            for n in (report.notes or [])
        ],
    }
