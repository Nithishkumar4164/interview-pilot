"""Report API routes.

Endpoints:
  GET  /api/reports/:id          — Get a report
  POST /api/reports/:id/notes    — Add a note to a report
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.models.report import ReportNote
from src.repositories.report_repo import ReportNoteRepository, ReportRepository
from src.schemas.report import AddNoteRequest
from src.utils.logging import get_logger

logger = get_logger("api.reports")

router = APIRouter(prefix="/reports", tags=["reports"])

# Default dev user UUID
_DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
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
) -> dict:
    """Add a note to a report."""
    # Verify report exists
    report_repo = ReportRepository(db)
    report = await report_repo.get_by_id(uuid.UUID(report_id))

    if report is None:
        raise HTTPException(
            status_code=404,
            detail=f"Report {report_id} not found.",
        )

    note_repo = ReportNoteRepository(db)
    note = ReportNote(
        id=uuid.uuid4(),
        report_id=uuid.UUID(report_id),
        user_id=_DEV_USER_ID,
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
