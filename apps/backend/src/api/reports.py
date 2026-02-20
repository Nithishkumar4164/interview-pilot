"""Report API routes.

Endpoints:
  GET  /api/reports/:id          — Get a report
  POST /api/reports/:id/notes    — Add a note to a report
"""

from fastapi import APIRouter, HTTPException

from src.schemas.report import AddNoteRequest, ReportResponse
from src.utils.logging import get_logger

logger = get_logger("api.reports")

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(report_id: str) -> dict:
    """Get a generated interview report.

    In production, this queries the DB. For now, returns a placeholder
    since reports are generated on-demand at session end.
    """
    # TODO: Wire to DB via ReportRepository
    raise HTTPException(
        status_code=404,
        detail=f"Report {report_id} not found. Reports are generated when a session ends.",
    )


@router.post("/{report_id}/notes")
async def add_note(report_id: str, request: AddNoteRequest) -> dict:
    """Add a note to a report."""
    # TODO: Wire to DB via ReportRepository
    logger.info(
        "note_added",
        report_id=report_id,
        note_length=len(request.note_text),
    )
    return {"status": "ok", "message": "Note added (stub)"}
