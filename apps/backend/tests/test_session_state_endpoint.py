"""GET /api/sessions/{id}/state.

The snapshot comes from the session's checkpoints in Postgres, so any backend
process can serve it. Once the session has ended and its checkpoints are
deleted there is no snapshot (the UI uses it only to restore a live
session's question after a reload).
"""

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import psycopg
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src import dependencies
from src.config import settings
from src.graphs.code_graph import code_thread_id
from src.main import app
from src.models.report import Report
from src.models.session import InterviewSession
from src.models.transcript import Transcript
from src.replay.runner import build_scripted_failover_manager
from src.schemas.transcript import SpeakerRole, TranscriptEventSchema
from src.services.access_control import DEV_USER_ID
from src.websocket import audio_handler
from src.websocket.manager import dashboard_manager
from src.workers.langgraph_session import LangGraphSessionPipeline, thread_id_for


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@pytest.fixture
async def env(monkeypatch) -> AsyncIterator[dict]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            yield session
            await session.commit()

    async def _no_report(**_kwargs) -> None:
        return None

    from src.api import sessions as sessions_api

    app.dependency_overrides[dependencies.get_db] = _get_db
    monkeypatch.setattr(settings, "deepgram_api_key", "")
    monkeypatch.setattr(sessions_api, "_generate_report_background", _no_report)
    # Pipelines are built with the failover manager audio_handler imported at load time.
    monkeypatch.setattr(audio_handler, "failover_manager", build_scripted_failover_manager())
    monkeypatch.setattr(dependencies, "failover_manager", build_scripted_failover_manager())
    await dependencies.checkpointer.open(settings.database_url_sync)

    sid = uuid.uuid4()
    async with factory() as db:
        db.add(
            InterviewSession(
                id=sid, user_id=DEV_USER_ID, status="live", level="sde2", duration_minutes=45, focus_areas=["coding"]
            )
        )
        await db.commit()
    yield {"sid": str(sid)}

    app.dependency_overrides.pop(dependencies.get_db, None)
    audio_handler._active_sessions.pop(str(sid), None)
    audio_handler._active_workers.pop(str(sid), None)
    await dependencies.checkpointer.delete_threads(thread_id_for(str(sid)), code_thread_id(str(sid)))
    await dependencies.checkpointer.close()
    async with factory() as db:
        for model in (Report, Transcript):
            await db.execute(delete(model).where(model.session_id == sid))
        await db.execute(delete(InterviewSession).where(InterviewSession.id == sid))
        await db.commit()
    await engine.dispose()


async def _get_state(sid: str) -> dict | None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/sessions/{sid}/state")
    assert response.status_code == 200, response.text
    return response.json()["snapshot"]


async def test_state_snapshot_comes_from_the_checkpoints_until_the_session_ends(env) -> None:
    sid = env["sid"]
    pipeline = audio_handler.get_session_pipeline(sid)  # what the audio/code sockets do
    assert isinstance(pipeline, LangGraphSessionPipeline)

    with (
        patch.object(dashboard_manager, "send_transcript", AsyncMock()),
        patch.object(dashboard_manager, "send_analysis", AsyncMock()),
        patch.object(dashboard_manager, "send_code_analysis", AsyncMock()),
    ):
        await pipeline.on_question_change("Reverse a linked list.", {"id": "q-rev"}, 2)
        await pipeline.on_transcript_event(
            TranscriptEventSchema(
                id=f"{sid}-0",
                session_id=sid,
                speaker_role=SpeakerRole.CANDIDATE,
                text="I'd walk the list once, flipping each next pointer to the previous node.",
                is_final=True,
                confidence=0.9,
                timestamp="2026-01-01T00:00:00+00:00",
                channel="microphone",
            )
        )
        await pipeline.on_code_snapshot("def reverse(head):\n    prev = None\n", "python", "codemirror")
        await pipeline.wait_idle()

        live = await _get_state(sid)
        assert live is not None
        assert live["plan_progress"]["current_question_index"] == 2  # what the frontend restores
        assert live["active_question_id"] == "q-rev"
        assert (live["transcript_count"], live["analysis_count"], live["code_snapshot_count"]) == (1, 1, 1)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post(f"/api/sessions/{sid}/end")).status_code == 200

    assert await _get_state(sid) is None  # ended: checkpoints deleted, nothing to restore


async def test_ending_from_a_process_without_the_pipeline_still_tears_down(env, monkeypatch) -> None:
    """End-session can run in a process that never held the session's pipeline
    (after a restart, or on another worker). It must still delete the
    session's checkpoints, and give the report the session's memory, read
    from the checkpoints."""
    from src.api import sessions as sessions_api

    sid = env["sid"]
    reports: list[dict] = []

    async def capture_report(**kwargs) -> None:
        reports.append(kwargs)

    monkeypatch.setattr(sessions_api, "_generate_report_background", capture_report)
    pipeline = audio_handler.get_session_pipeline(sid)
    with (
        patch.object(dashboard_manager, "send_transcript", AsyncMock()),
        patch.object(dashboard_manager, "send_analysis", AsyncMock()),
        patch.object(dashboard_manager, "send_code_analysis", AsyncMock()),
    ):
        await pipeline.on_question_change("Reverse a linked list.", {"id": "q-rev"}, 2)
        await pipeline.on_transcript_event(
            TranscriptEventSchema(
                id=f"{sid}-0",
                session_id=sid,
                speaker_role=SpeakerRole.CANDIDATE,
                text=CLAIM,
                is_final=True,
                confidence=0.9,
                timestamp="2026-01-01T00:00:00+00:00",
                channel="microphone",
            )
        )
        await pipeline.on_code_snapshot("def reverse(head):\n    prev = None\n", "python", "codemirror")
        await pipeline.wait_idle()
    # This process forgets the pipeline: end-session runs where it never lived.
    audio_handler._active_workers.pop(sid)
    audio_handler._active_sessions.pop(sid)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(f"/api/sessions/{sid}/end")).status_code == 200

    with psycopg.connect(settings.database_url_sync) as conn:
        left = conn.execute(
            "SELECT count(*) FROM checkpoints WHERE thread_id IN (%s, %s)", (sid, code_thread_id(sid))
        ).fetchone()[0]
    assert left == 0, "the ended session's checkpoints were left behind"
    snapshots = reports[0]["snapshots"]
    assert CLAIM in snapshots["fact_store"]["claimed_experience"]
    assert len(snapshots["analysis_results"]) == 1
    assert snapshots["code_progress"] != "unknown"


CLAIM = (
    "I have used Kafka for about four years on the ingestion side and I built the consumer framework "
    "our teams still use for exactly-once processing."
)
