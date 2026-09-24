"""PersistenceService batch flushes against a real Postgres.

Each test creates its own interview_sessions row (FK target) and deletes
everything it wrote afterwards.
"""

import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import settings
from src.models.analysis import AnalysisEvent, CodeSnapshot
from src.models.hint import HintEvent
from src.models.session import InterviewSession
from src.models.transcript import Transcript
from src.services.persistence_service import PersistenceService

DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@pytest.fixture
async def db():
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_id = uuid.uuid4()
    async with factory() as s:
        s.add(InterviewSession(id=session_id, user_id=DEV_USER_ID, level="mid", duration_minutes=30, focus_areas=[]))
        await s.commit()
    yield factory, str(session_id)
    async with factory() as s:
        for model in (AnalysisEvent, HintEvent, CodeSnapshot, Transcript):
            await s.execute(delete(model).where(model.session_id == session_id))
        await s.execute(delete(InterviewSession).where(InterviewSession.id == session_id))
        await s.commit()
    await engine.dispose()


async def _count(factory, model, session_id: str) -> int:
    async with factory() as s:
        return await s.scalar(select(func.count()).select_from(model).where(model.session_id == uuid.UUID(session_id)))


def _transcript(svc: PersistenceService, session_id: str, text: str, **kw) -> None:
    svc.enqueue_transcript(
        session_id,
        speaker_role="candidate",
        text=text,
        is_final=True,
        confidence=0.9,
        timestamp=datetime.now(UTC),
        **kw,
    )


async def test_duplicate_row_does_not_drop_the_rest_of_the_batch(db) -> None:
    """A re-emitted effect (e.g. a graph node re-run on resume) must not lose unrelated rows.

    Uses a repeated hint_id, which is the hint's primary key.
    """
    factory, session_id = db
    svc = PersistenceService(factory)
    hint_id = str(uuid.uuid4())

    _transcript(svc, session_id, "first")
    for _ in range(2):
        svc.enqueue_hint(
            session_id, question_id="q1", level=1, text="h", displayed_at=datetime.now(UTC), hint_id=hint_id
        )
    _transcript(svc, session_id, "second")
    await svc._flush_batch()

    assert await _count(factory, Transcript, session_id) == 2
    assert await _count(factory, HintEvent, session_id) == 1


async def test_idempotency_key_dedupes_within_and_across_flushes(db) -> None:
    factory, session_id = db
    svc = PersistenceService(factory)
    key = f"{session_id}:utt-1:analysis"

    for _ in range(2):  # within one batch
        svc.enqueue_analysis(session_id, answer_quality=3, on_track=True, insight="i", idempotency_key=key)
    await svc._flush_batch()
    svc.enqueue_analysis(session_id, answer_quality=3, on_track=True, insight="i", idempotency_key=key)
    await svc._flush_batch()  # across flushes, e.g. after a resumed graph run

    assert await _count(factory, AnalysisEvent, session_id) == 1


async def test_rows_without_a_key_are_never_deduplicated(db) -> None:
    factory, session_id = db
    svc = PersistenceService(factory)
    for _ in range(3):
        _transcript(svc, session_id, "same text")
    await svc._flush_batch()
    assert await _count(factory, Transcript, session_id) == 3


async def test_flushed_rows_match_what_was_enqueued(db) -> None:
    """The Core insert must write the same values the ORM path did, including defaults."""
    factory, session_id = db
    svc = PersistenceService(factory)
    ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    _transcript(svc, session_id, "hello", channel="mic", root_speaker_id="rs-1", idempotency_key="k-t")
    svc.enqueue_analysis(
        session_id,
        answer_quality=4,
        on_track=False,
        insight="ins",
        red_flags=["rf"],
        suggestion="sug",
        hint_signal="nudge",
        dimensions={"relevance": {"score": "good"}},
    )
    svc.enqueue_hint(session_id, question_id="q9", level=2, text="ht", displayed_at=ts)
    svc.enqueue_code_snapshot(session_id, code="x = 1", language="python", timestamp=ts)
    await svc._flush_batch()

    sid = uuid.UUID(session_id)
    async with factory() as s:
        t = (await s.scalars(select(Transcript).where(Transcript.session_id == sid))).one()
        a = (await s.scalars(select(AnalysisEvent).where(AnalysisEvent.session_id == sid))).one()
        h = (await s.scalars(select(HintEvent).where(HintEvent.session_id == sid))).one()
        c = (await s.scalars(select(CodeSnapshot).where(CodeSnapshot.session_id == sid))).one()

    assert (t.text, t.channel, t.root_speaker_id, t.speech_vector_id, t.is_retroactive, t.idempotency_key) == (
        "hello",
        "mic",
        "rs-1",
        "",
        False,
        "k-t",
    )
    assert (a.answer_quality, a.on_track, a.red_flags, a.hint_signal, a.dimensions, a.idempotency_key) == (
        4,
        False,
        ["rf"],
        "nudge",
        {"relevance": {"score": "good"}},
        None,
    )
    # Python-side column defaults the ORM used to apply at flush time.
    assert (h.level, h.displayed_at, h.interviewer_used, h.candidate_response_quality) == (2, ts, False, "none")
    assert (c.code, c.progress_state, c.timestamp) == ("x = 1", "unknown", ts)
    # Server-side defaults filled by the DB.
    assert all(row.created_at is not None and row.updated_at is not None for row in (t, a, h, c))
