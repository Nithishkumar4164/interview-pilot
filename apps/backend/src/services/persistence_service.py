"""Async queue-based batch writer for persisting session data.

Decouples real-time WebSocket path from DB I/O by buffering writes
in an asyncio queue and flushing them in batches.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.analysis import AnalysisEvent, CodeSnapshot
from src.models.hint import HintEvent
from src.models.transcript import Transcript
from src.utils.logging import get_logger

logger = get_logger("persistence")

FLUSH_INTERVAL_S = 2.0
MAX_BATCH_SIZE = 50


class PersistenceService:
    """Queue-based async batch writer for interview session data.

    Usage:
        service = PersistenceService(async_session_factory)
        await service.start()
        service.enqueue_transcript(session_id, data)
        ...
        await service.stop()
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._flush_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background flush loop."""
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("persistence_service_started")

    async def stop(self) -> None:
        """Stop the flush loop and drain remaining items."""
        self._running = False
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # Final drain
        await self._flush_batch()
        logger.info("persistence_service_stopped")

    def enqueue_transcript(
        self,
        session_id: str,
        *,
        speaker_role: str,
        text: str,
        is_final: bool,
        confidence: float,
        timestamp: str,
        channel: str = "tab",
        is_retroactive: bool = False,
        word_timestamps: dict | None = None,
    ) -> None:
        """Enqueue a transcript for persistence."""
        entity = Transcript(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            speaker_role=speaker_role,
            text=text,
            is_final=is_final,
            confidence=confidence,
            timestamp=timestamp,
            channel=channel,
            is_retroactive=is_retroactive,
            word_timestamps=word_timestamps,
        )
        self._queue.put_nowait(entity)

    def enqueue_analysis(
        self,
        session_id: str,
        *,
        answer_quality: int,
        on_track: bool,
        insight: str,
        red_flags: list[str] | None = None,
        suggestion: str | None = None,
        hint_signal: str | None = None,
    ) -> None:
        """Enqueue an analysis event for persistence."""
        entity = AnalysisEvent(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            answer_quality=answer_quality,
            on_track=on_track,
            insight=insight,
            red_flags=red_flags,
            suggestion=suggestion,
            hint_signal=hint_signal,
        )
        self._queue.put_nowait(entity)

    def enqueue_hint(
        self,
        session_id: str,
        *,
        question_id: str,
        level: int,
        text: str,
        displayed_at: str,
        hint_id: str | None = None,
    ) -> None:
        """Enqueue a hint event for persistence."""
        entity = HintEvent(
            id=uuid.UUID(hint_id) if hint_id else uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            question_id=question_id,
            level=level,
            text=text,
            displayed_at=displayed_at,
        )
        self._queue.put_nowait(entity)

    def enqueue_code_snapshot(
        self,
        session_id: str,
        *,
        code: str,
        language: str,
        timestamp: str,
        diff: str | None = None,
        approach: str | None = None,
        progress_state: str = "unknown",
    ) -> None:
        """Enqueue a code snapshot for persistence."""
        entity = CodeSnapshot(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            code=code,
            language=language,
            timestamp=timestamp,
            diff=diff,
            approach=approach,
            progress_state=progress_state,
        )
        self._queue.put_nowait(entity)

    async def _flush_loop(self) -> None:
        """Background loop that flushes the queue periodically."""
        while self._running:
            await asyncio.sleep(FLUSH_INTERVAL_S)
            await self._flush_batch()

    async def _flush_batch(self) -> None:
        """Drain up to MAX_BATCH_SIZE items and write them in a single transaction."""
        batch: list[Any] = []
        while not self._queue.empty() and len(batch) < MAX_BATCH_SIZE:
            try:
                item = self._queue.get_nowait()
                batch.append(item)
            except asyncio.QueueEmpty:
                break

        if not batch:
            return

        try:
            async with self._session_factory() as db:
                db.add_all(batch)
                await db.commit()
            logger.info("batch_flushed", count=len(batch))
        except Exception as e:
            logger.error("batch_flush_failed", count=len(batch), error=str(e))
