"""Session history and report lookup API routes.

Endpoints:
  GET /api/history/sessions                  — List sessions with report/plan data
  GET /api/history/sessions/:id/report       — Get report for a completed session
  GET /api/history/sessions/:id/detail       — Get expanded detail for a session
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.authz import authorized_session, get_user_principal
from src.dependencies import get_db
from src.models.plan import InterviewPlan
from src.models.report import Report
from src.models.session import InterviewSession
from src.repositories.report_repo import ReportRepository
from src.repositories.session_repo import SessionRepository
from src.services.access_control import Principal
from src.utils.logging import get_logger

logger = get_logger("api.history")

router = APIRouter(prefix="/history", tags=["history"])


def _extract_report_fields(reports: list) -> dict:
    """Pick the latest report and extract summary fields."""
    if not reports:
        return {
            "report_id": None,
            "report_status": None,
            "recommendation": None,
            "confidence_level": None,
        }
    # Sort by created_at descending, pick latest
    latest = sorted(reports, key=lambda r: r.created_at or r.id, reverse=True)[0]
    return {
        "report_id": str(latest.id),
        "report_status": latest.status,
        "recommendation": latest.recommendation,
        "confidence_level": latest.confidence_level,
    }


def _extract_plan_id(plans: list) -> str | None:
    """Pick the active plan ID."""
    active = [p for p in plans if p.is_active]
    if active:
        return str(active[0].id)
    if plans:
        return str(plans[0].id)
    return None


@router.get("/sessions")
async def list_sessions(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_user_principal),
) -> dict:
    """List the caller's sessions (all sessions for admins) with report/plan data."""
    session_repo = SessionRepository(db)
    sessions, total = await session_repo.get_sessions_enriched(
        None if principal.is_admin else principal.user_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )

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
                "candidate_name": s.candidate_name,
                "candidate_email": s.candidate_email,
                "created_at": s.created_at.isoformat() if s.created_at else "",
                **_extract_report_fields(s.reports),
                "plan_id": _extract_plan_id(s.plans),
            }
            for s in sessions
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/sessions/{session_id}/detail")
async def get_session_detail(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    session: InterviewSession = Depends(authorized_session),
) -> dict:
    """Get expanded detail for a session: plan, report summary, stats."""
    sid = session.id

    # Plan data
    plan_result = await db.execute(
        select(InterviewPlan)
        .where(InterviewPlan.session_id == sid)
        .where(InterviewPlan.is_active == True)  # noqa: E712
        .order_by(InterviewPlan.version.desc())
        .limit(1)
    )
    plan = plan_result.scalar_one_or_none()

    plan_summary = None
    if plan and plan.plan_data:
        phases_data = plan.plan_data.get("phases", [])
        plan_summary = {
            "id": str(plan.id),
            "phases": [
                {
                    "phase": p.get("phase", ""),
                    "start_minute": p.get("start_minute", 0),
                    "duration_minutes": p.get("duration_minutes", 0),
                    "questions": [
                        {
                            "id": q.get("id", ""),
                            "text": q.get("text", ""),
                            "category": q.get("category", ""),
                            "difficulty": q.get("difficulty", 0),
                            "time_minutes": q.get("time_minutes", 0),
                        }
                        for q in p.get("questions", [])
                    ],
                }
                for p in phases_data
            ],
        }

    # Report summary
    report_result = await db.execute(
        select(Report).where(Report.session_id == sid).order_by(Report.created_at.desc()).limit(1)
    )
    report = report_result.scalar_one_or_none()

    report_summary = None
    if report and report.status == "completed":
        rd = report.report_data or {}
        report_summary = {
            "id": str(report.id),
            "recommendation": report.recommendation,
            "confidence_level": report.confidence_level,
            "scores": [{"criterion": s.get("criterion", ""), "score": s.get("score", 0)} for s in rd.get("scores", [])],
            "key_moments_count": len(rd.get("key_moments", [])),
            "strengths": rd.get("strengths", []),
            "areas_for_growth": rd.get("areas_for_growth", []),
        }

    # Stats — use count queries for efficiency
    from src.models.hint import HintEvent
    from src.models.transcript import Transcript

    transcript_count_result = await db.execute(
        select(func.count(Transcript.id)).where(
            Transcript.session_id == sid,
            Transcript.is_final == True,  # noqa: E712
        )
    )
    transcript_count = transcript_count_result.scalar() or 0

    from src.models.analysis import AnalysisEvent

    analysis_count_result = await db.execute(
        select(func.count(AnalysisEvent.id)).where(AnalysisEvent.session_id == sid)
    )
    analysis_count = analysis_count_result.scalar() or 0

    hint_count_result = await db.execute(select(func.count(HintEvent.id)).where(HintEvent.session_id == sid))
    hint_count = hint_count_result.scalar() or 0

    return {
        "session_id": session_id,
        "plan": plan_summary,
        "report": report_summary,
        "stats": {
            "transcript_count": transcript_count,
            "analysis_count": analysis_count,
            "hint_count": hint_count,
        },
    }


@router.get("/sessions/{session_id}/report")
async def get_session_report(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _session: InterviewSession = Depends(authorized_session),
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
