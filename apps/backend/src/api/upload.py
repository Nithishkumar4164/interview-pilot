from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_current_user
from src.dependencies import get_db
from src.main import failover_manager
from src.models.user import User
from src.schemas.upload import ExtractionStatusResponse, UploadResponse
from src.services.resume_service import ResumeService
from src.services.storage_service import ALLOWED_CONTENT_TYPES, MAX_FILE_SIZE_BYTES, StorageService

router = APIRouter(prefix="/upload", tags=["upload"])

# In-memory extraction cache (production would use Redis or DB)
_extraction_cache: dict[str, dict] = {}


def _get_resume_service() -> ResumeService:
    storage = StorageService()
    return ResumeService(storage=storage, llm=failover_manager)


@router.post("/resume", response_model=UploadResponse)
async def upload_resume(
    file: UploadFile,
    user: User = Depends(get_current_user),
) -> UploadResponse:
    """Upload a resume (PDF or DOCX, max 10MB) and start extraction."""
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {file.content_type}. Allowed: PDF, DOCX",
        )

    content = await file.read()

    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE_BYTES // (1024*1024)}MB",
        )

    service = _get_resume_service()

    try:
        reference_id = service.upload_resume(content, file.content_type, file.filename or "resume")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Start extraction asynchronously
    _extraction_cache[reference_id] = {"status": "processing"}
    try:
        structured = await service.process_resume(content)
        _extraction_cache[reference_id] = {"status": "completed", "data": structured}
    except Exception as e:
        _extraction_cache[reference_id] = {"status": "failed", "error": str(e)}

    return UploadResponse(
        reference_id=reference_id,
        filename=file.filename or "resume",
        file_type=file.content_type,
        size_bytes=len(content),
    )


@router.get("/{reference_id}/status", response_model=ExtractionStatusResponse)
async def get_extraction_status(
    reference_id: str,
    user: User = Depends(get_current_user),
) -> ExtractionStatusResponse:
    """Check the status of resume extraction."""
    cache_entry = _extraction_cache.get(reference_id)

    if cache_entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reference ID not found",
        )

    return ExtractionStatusResponse(
        reference_id=reference_id,
        status=cache_entry["status"],
        structured_data=cache_entry.get("data"),
        error=cache_entry.get("error"),
    )
