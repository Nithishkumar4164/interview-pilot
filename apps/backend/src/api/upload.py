import asyncio
import json
import uuid
from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, UploadFile, status
from starlette.responses import StreamingResponse

from src.config import settings
from src.dependencies import failover_manager
from src.schemas.upload import ExtractionStatusResponse, UploadResponse
from src.services.resume_service import ResumeService
from src.services.storage_service import (
    ALLOWED_CONTENT_TYPES,
    MAX_FILE_SIZE_BYTES,
    StorageService,
    sanitize_filename,
)
from src.utils.logging import get_logger

router = APIRouter(prefix="/upload", tags=["upload"])
logger = get_logger("upload")

_CACHE_PREFIX = "extraction:"
_CACHE_TTL = 300  # 5 minutes

_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def _cache_set(reference_id: str, data: dict) -> None:
    r = await _get_redis()
    await r.set(f"{_CACHE_PREFIX}{reference_id}", json.dumps(data), ex=_CACHE_TTL)


async def _cache_get(reference_id: str) -> dict | None:
    r = await _get_redis()
    raw = await r.get(f"{_CACHE_PREFIX}{reference_id}")
    if raw is None:
        return None
    return json.loads(raw)


def _get_resume_service() -> ResumeService:
    """Create a ResumeService. Uses local-only storage when S3 is unavailable."""
    try:
        storage = StorageService()
        storage.ensure_bucket()
        return ResumeService(storage=storage, llm=failover_manager)
    except Exception:
        logger.warning("s3_unavailable", detail="Using local-only mode for resume processing")
        return ResumeService(storage=None, llm=failover_manager)


@router.post("/resume", response_model=UploadResponse)
async def upload_resume(
    file: UploadFile,
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
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB",
        )

    service = _get_resume_service()
    safe_name = sanitize_filename(file.filename)

    # Generate reference ID locally (skip S3 if unavailable)
    reference_id = str(uuid.uuid4())
    if service.storage is not None:
        try:
            reference_id = service.upload_resume(content, file.content_type, safe_name)
        except Exception as e:
            logger.warning("s3_upload_failed", error=str(e), detail="Continuing with local extraction only")

    # Start extraction in background so the upload returns immediately
    await _cache_set(reference_id, {"status": "processing"})

    async def _run_extraction() -> None:
        try:
            structured = await service.process_resume(content)
            await _cache_set(reference_id, {"status": "completed", "data": structured})
        except Exception as e:
            logger.error("extraction_failed", error=str(e))
            await _cache_set(reference_id, {"status": "failed", "error": str(e)})

    asyncio.create_task(_run_extraction())

    return UploadResponse(
        reference_id=reference_id,
        filename=safe_name,
        file_type=file.content_type,
        size_bytes=len(content),
    )


@router.get("/{reference_id}/status", response_model=ExtractionStatusResponse)
async def get_extraction_status(
    reference_id: str,
) -> ExtractionStatusResponse:
    """Check the status of resume extraction."""
    cache_entry = await _cache_get(reference_id)

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


SSE_TIMEOUT_SECONDS = 120


async def _sse_event_generator(reference_id: str) -> AsyncGenerator[str, None]:
    """Yield SSE events for extraction progress."""
    elapsed = 0
    while elapsed < SSE_TIMEOUT_SECONDS:
        cache_entry = await _cache_get(reference_id)

        if cache_entry is None:
            yield f"event: error\ndata: {json.dumps({'status': 'failed', 'error': 'Reference ID not found'})}\n\n"
            return

        entry_status = cache_entry["status"]

        if entry_status == "completed":
            yield f"event: completed\ndata: {json.dumps({'status': 'completed', 'structured_data': cache_entry.get('data')})}\n\n"
            return

        if entry_status == "failed":
            yield f"event: error\ndata: {json.dumps({'status': 'failed', 'error': cache_entry.get('error', 'Unknown error')})}\n\n"
            return

        # Still processing
        yield f"event: status\ndata: {json.dumps({'status': 'processing'})}\n\n"
        await asyncio.sleep(1)
        elapsed += 1

    # Timeout
    yield f"event: error\ndata: {json.dumps({'status': 'failed', 'error': 'Extraction timed out'})}\n\n"


@router.get("/{reference_id}/stream")
async def stream_extraction_status(reference_id: str) -> StreamingResponse:
    """Stream extraction status via Server-Sent Events."""
    cache_entry = await _cache_get(reference_id)
    if cache_entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reference ID not found",
        )

    return StreamingResponse(
        _sse_event_generator(reference_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
