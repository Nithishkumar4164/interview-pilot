"""Code snapshots sent over the code WebSocket reach the DB without breaking the batch they share.

Before the fix the handler passed the detected-approach *dict* into the
``code_snapshots.approach`` VARCHAR column. The persistence flush writes a
batch in one transaction, so the failed insert also rolled back every
transcript and analysis queued in the same two-second window.
"""

import asyncio
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.main import app
from src.models.analysis import CodeSnapshot
from src.models.session import InterviewSession
from src.models.transcript import Transcript
from src.services.access_control import DEV_USER_ID
from src.services.code_analysis_service import detect_progress
from src.services.persistence_service import PersistenceService


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")

TWO_SUM = (
    "def two_sum(nums, target):\n"
    "    seen = {}\n"
    "    for i, n in enumerate(nums):\n"
    "        if target - n in seen:\n"
    "            return [seen[target - n], i]\n"
    "        seen[n] = i\n"
)


async def _with_db(fn):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        return await fn(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


def test_code_snapshot_and_transcript_in_one_batch_are_both_persisted(monkeypatch) -> None:
    sid = uuid.uuid4()

    async def create(factory):
        async with factory() as db:
            db.add(
                InterviewSession(
                    id=sid,
                    user_id=DEV_USER_ID,
                    status="live",
                    level="sde2",
                    duration_minutes=45,
                    focus_areas=["coding"],
                )
            )
            await db.commit()

    asyncio.run(_with_db(create))
    queued = PersistenceService(None)  # type: ignore[arg-type]  # only queues; flushed below with a fresh engine
    monkeypatch.setattr("src.dependencies.get_persistence_service", lambda: queued)

    try:
        queued.enqueue_transcript(
            str(sid),
            speaker_role="candidate",
            text="I'll keep a hash map from value to index.",
            is_final=True,
            confidence=0.9,
            timestamp="2026-01-01T00:00:00+00:00",
        )
        with TestClient(app).websocket_connect(f"/ws/code/{sid}") as ws:
            ws.send_json({"type": "code_snapshot", "payload": {"code": TWO_SUM, "language": "python"}})
            ws.send_json({"type": "ping", "timestamp": 1})
            assert ws.receive_json()["type"] == "pong"  # the snapshot was handled before the ping

        async def flush_and_read(factory):
            queued._session_factory = factory
            await queued._flush_batch()
            async with factory() as db:
                transcripts = (await db.execute(select(Transcript.text).where(Transcript.session_id == sid))).all()
                code = (
                    await db.execute(
                        select(CodeSnapshot.approach, CodeSnapshot.progress_state).where(CodeSnapshot.session_id == sid)
                    )
                ).all()
            return transcripts, code

        transcripts, code = asyncio.run(_with_db(flush_and_read))
        assert [t.text for t in transcripts] == ["I'll keep a hash map from value to index."]
        assert [(c.approach, c.progress_state) for c in code] == [("hash_map", detect_progress(TWO_SUM).value)]
    finally:

        async def cleanup(factory):
            async with factory() as db:
                for model in (Transcript, CodeSnapshot):
                    await db.execute(delete(model).where(model.session_id == sid))
                await db.execute(delete(InterviewSession).where(InterviewSession.id == sid))
                await db.commit()

        asyncio.run(_with_db(cleanup))
