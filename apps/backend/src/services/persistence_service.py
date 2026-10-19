"""Async queue-based batch writer for persisting session data.

Decouples real-time WebSocket path from DB I/O by buffering writes
in an asyncio queue and flushing them in batches.
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.analysis import AnalysisEvent, CodeSnapshot
from src.models.hint import HintEvent
from src.models.transcript import Transcript
from src.utils.logging import get_logger

logger = get_logger("persistence")

FLUSH_INTERVAL_S = 2.0
_CODE_SNAPSHOT_ID_NAMESPACE = uuid.UUID("0b8f3f8e-6c1e-4a55-9a1e-6d3f2c7b9e41")
MAX_BATCH_SIZE = 50


def _to_datetime(value: str | datetime | None) -> datetime:
    """Coerce an event-time field to a timezone-aware :class:`datetime`.

    Accepts ISO-8601 strings (with or without trailing ``Z``), existing
    ``datetime`` instances (naive ones are assumed UTC), or ``None`` which
    defaults to ``datetime.now(UTC)``.
    """
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _row(entity: Any) -> dict[str, Any]:
    """Column values of an un-flushed ORM entity, with Python-side scalar defaults applied.

    Server-side defaults (created_at/updated_at) are omitted so the DB fills them.
    """
    row: dict[str, Any] = {}
    for attr in sa_inspect(type(entity)).column_attrs:
        column = attr.columns[0]
        value = getattr(entity, attr.key)
        if value is None and column.default is not None and column.default.is_scalar:
            value = column.default.arg
        if value is None and column.server_default is not None:
            continue
        row[column.key] = value
    return row


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
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
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
        timestamp: str | datetime,
        channel: str = "tab",
        is_retroactive: bool = False,
        word_timestamps: dict | None = None,
        root_speaker_id: str = "",
        speech_vector_id: str = "",
        idempotency_key: str | None = None,
    ) -> None:
        """Enqueue a transcript for persistence."""
        entity = Transcript(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            speaker_role=speaker_role,
            text=text,
            is_final=is_final,
            confidence=confidence,
            timestamp=_to_datetime(timestamp),
            channel=channel,
            is_retroactive=is_retroactive,
            word_timestamps=word_timestamps,
            root_speaker_id=root_speaker_id,
            speech_vector_id=speech_vector_id,
            idempotency_key=idempotency_key,
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
        dimensions: dict | None = None,
        idempotency_key: str | None = None,
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
            dimensions=dimensions,
            idempotency_key=idempotency_key,
        )
        self._queue.put_nowait(entity)

    def enqueue_hint(
        self,
        session_id: str,
        *,
        question_id: str,
        level: int,
        text: str,
        displayed_at: str | datetime,
        hint_id: str | None = None,
        idempotency_key: str | None = None,
        interviewer_used: bool = False,
    ) -> None:
        """Enqueue a hint event for persistence."""
        entity = HintEvent(
            id=uuid.UUID(hint_id) if hint_id else uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            question_id=question_id,
            level=level,
            text=text,
            displayed_at=_to_datetime(displayed_at),
            idempotency_key=idempotency_key,
            interviewer_used=interviewer_used,
        )
        self._queue.put_nowait(entity)

    def enqueue_code_snapshot(
        self,
        session_id: str,
        *,
        code: str,
        language: str,
        timestamp: str | datetime,
        diff: str | None = None,
        approach: str | None = None,
        progress_state: str = "unknown",
        idempotency_key: str | None = None,
    ) -> None:
        """Enqueue a code snapshot for persistence.

        ``code_snapshots`` has no idempotency column; a key instead fixes the
        row's primary key, so a re-emitted snapshot hits ON CONFLICT DO NOTHING.
        """
        entity = CodeSnapshot(
            id=uuid.uuid5(_CODE_SNAPSHOT_ID_NAMESPACE, idempotency_key) if idempotency_key else uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            code=code,
            language=language,
            timestamp=_to_datetime(timestamp),
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

        # One INSERT ... ON CONFLICT DO NOTHING per table, in first-seen order.
        # A re-emitted event (same idempotency_key or primary key) is skipped
        # instead of failing the transaction and losing the whole batch.
        rows_by_model: dict[type, list[dict[str, Any]]] = {}
        for entity in batch:
            rows_by_model.setdefault(type(entity), []).append(_row(entity))

        try:
            inserted = 0
            async with self._session_factory() as db:
                for model, rows in rows_by_model.items():
                    result = await db.execute(pg_insert(model).values(rows).on_conflict_do_nothing())
                    inserted += cast(CursorResult[Any], result).rowcount
                await db.commit()
            logger.info("batch_flushed", count=len(batch), inserted=inserted, skipped=len(batch) - inserted)
        except Exception as e:
            logger.error("batch_flush_failed", count=len(batch), error=str(e))


_VALID_ENQUEUE_EVENTS: frozenset[str] = frozenset({"transcript", "analysis", "hint", "code_snapshot"})


def safe_enqueue(event_type: str, session_id: str, **kwargs: Any) -> None:
    """Fire-and-forget helper that dispatches to the global
    PersistenceService.

    Replaces the 5+ duplicated try/import/enqueue blocks across workers,
    services, and handlers.

    ``event_type`` MUST be one of :data:`_VALID_ENQUEUE_EVENTS`
    (``transcript``, ``analysis``, ``hint``, ``code_snapshot``). Unknown
    values raise :class:`ValueError` — the whitelist catches typos like
    ``"transcrpt"`` that would otherwise be silently swallowed by the
    fire-and-forget except below.

    Downstream runtime errors (bad UUID strings from test fixtures, full
    queue, model-constructor mismatches) are logged and swallowed so a
    single malformed event can't crash the hot transcript path.
    """
    if event_type not in _VALID_ENQUEUE_EVENTS:
        raise ValueError(f"Unknown event_type {event_type!r}. Valid: {sorted(_VALID_ENQUEUE_EVENTS)}")
    try:
        from src.dependencies import get_persistence_service

        service = get_persistence_service()
        method = getattr(service, f"enqueue_{event_type}")
        method(session_id, **kwargs)
    except Exception:
        logger.warning("persistence_enqueue_failed", type=event_type, exc_info=True)
