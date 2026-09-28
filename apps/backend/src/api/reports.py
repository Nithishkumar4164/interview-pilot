"""Report API routes.

Endpoints:
  GET  /api/reports/                — List all reports (paginated)
  GET  /api/reports/compare         — Compare multiple reports
  GET  /api/reports/:id             — Get a report
  GET  /api/reports/:id/status      — Get report generation status
  POST /api/reports/:id/notes       — Add a note to a report
"""

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.authz import authorized_report, authorized_session, get_user_principal
from src.dependencies import async_session_factory, failover_manager, get_db
from src.models.report import Report, ReportNote
from src.models.session import InterviewSession
from src.repositories.report_repo import ReportNoteRepository, ReportRepository
from src.schemas.report import AddNoteRequest
from src.services.access_control import Principal
from src.utils.logging import get_logger

logger = get_logger("api.reports")

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/generate/{session_id}")
async def generate_report_for_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    session: InterviewSession = Depends(authorized_session),
) -> dict:
    """Retroactively generate a report for an ended session.

    Useful when the original report generation failed or was interrupted.
    """

    if session.status != "ended":
        raise HTTPException(status_code=400, detail="Session must be in 'ended' status.")

    # Check if a completed report already exists
    report_repo = ReportRepository(db)
    existing = await report_repo.get_by_session(uuid.UUID(session_id))
    if existing and existing.status == "completed":
        return {"report_id": str(existing.id), "status": "already_exists"}

    # Calculate duration
    duration_minutes: float = session.duration_minutes
    if session.started_at and session.ended_at:
        elapsed = (session.ended_at - session.started_at).total_seconds() / 60.0
        duration_minutes = round(elapsed, 1)

    # Create placeholder report
    placeholder = Report(
        session_id=uuid.UUID(session_id),
        report_data={},
        recommendation="lean_hire",
        confidence_level=0.0,
        is_final=False,
        status="pending",
    )
    await report_repo.create(placeholder)
    await db.commit()

    report_id = str(placeholder.id)

    # Build snapshots from DB-only data (no in-memory services available)
    snapshots = {
        "session_id": session_id,
        "duration_minutes": duration_minutes,
        "target_level": session.level,
        "code_progress": "unknown",
    }

    # Fire background generation
    from src.api.sessions import _generate_report_background

    asyncio.create_task(
        _generate_report_background(
            report_id=report_id,
            session_id=session_id,
            snapshots=snapshots,
            fm=failover_manager,
            session_factory=async_session_factory,
        )
    )

    return {"report_id": report_id, "status": "pending"}


@router.get("/")
async def list_reports(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_user_principal),
) -> dict:
    """List the caller's reports (all reports for admins), newest first."""
    owner_id = None if principal.is_admin else principal.user_id
    report_repo = ReportRepository(db)
    reports = await report_repo.list_reports(limit=limit, offset=offset, owner_id=owner_id)
    total = await report_repo.count_reports(owner_id=owner_id)

    return {
        "reports": [
            {
                "id": str(r.id),
                "session_id": str(r.session_id),
                "recommendation": r.recommendation,
                "confidence_level": r.confidence_level,
                "status": r.status,
                "is_final": r.is_final,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "level": (r.report_data or {}).get("level", ""),
            }
            for r in reports
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/compare")
async def compare_reports(
    ids: str = Query(..., description="Comma-separated report IDs"),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_user_principal),
) -> dict:
    """Compare multiple reports side by side."""
    try:
        report_ids = list(dict.fromkeys(uuid.UUID(id.strip()) for id in ids.split(",") if id.strip()))
    except ValueError:
        raise HTTPException(status_code=404, detail="Report not found.") from None

    if len(report_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 report IDs required for comparison.")

    report_repo = ReportRepository(db)
    reports = await report_repo.get_multiple(report_ids, owner_id=None if principal.is_admin else principal.user_id)
    if len(reports) != len(report_ids):
        # Missing and not-yours are indistinguishable on purpose.
        raise HTTPException(status_code=404, detail="Report not found.")

    return {
        "reports": [
            {
                "id": str(r.id),
                "session_id": str(r.session_id),
                "recommendation": r.recommendation,
                "confidence_level": r.confidence_level,
                "is_final": r.is_final,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "scores": (r.report_data or {}).get("scores", []),
                "strengths": (r.report_data or {}).get("strengths", []),
                "areas_for_growth": (r.report_data or {}).get("areas_for_growth", []),
                "hint_summary": (r.report_data or {}).get("hint_summary", {}),
            }
            for r in reports
        ],
    }


@router.get("/{report_id}/status")
async def get_report_status(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    _report: Report = Depends(authorized_report),
) -> dict:
    """Get the generation status of a report."""
    report_repo = ReportRepository(db)
    report = await report_repo.get_by_id(uuid.UUID(report_id))

    if report is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")

    return {
        "id": str(report.id),
        "status": report.status,
        "is_final": report.is_final,
    }


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    _report: Report = Depends(authorized_report),
) -> dict:
    """Get a generated interview report."""
    report_repo = ReportRepository(db)
    report = await report_repo.get_by_id_with_notes(uuid.UUID(report_id))

    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"Report {report_id} not found.",
        )

    report_data = report.report_data or {}

    return {
        "id": str(report.id),
        "session_id": str(report.session_id),
        "status": report.status,
        "assessment": {
            "recommendation": report.recommendation,
            "confidence_level": report.confidence_level,
            "scores": report_data.get("scores", []),
            "key_moments": report_data.get("key_moments", []),
            "strengths": report_data.get("strengths", []),
            "areas_for_growth": report_data.get("areas_for_growth", []),
            "level_comparison": report_data.get("level_comparison", ""),
        },
        "hint_summary": report_data.get("hint_summary", {}),
        "annotated_transcript": report_data.get("annotated_transcript", []),
        "generated_at": report_data.get("generated_at", ""),
        "is_final": report.is_final,
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


@router.post("/{report_id}/notes")
async def add_note(
    report_id: str,
    request: AddNoteRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_user_principal),
    _report: Report = Depends(authorized_report),
) -> dict:
    """Add a note to a report."""
    note_repo = ReportNoteRepository(db)
    note = ReportNote(
        id=uuid.uuid4(),
        report_id=uuid.UUID(report_id),
        user_id=principal.user_id,
        note_text=request.note_text,
    )
    await note_repo.create(note)

    logger.info(
        "note_added",
        report_id=report_id,
        note_length=len(request.note_text),
    )

    return {
        "status": "ok",
        "note_id": str(note.id),
    }
