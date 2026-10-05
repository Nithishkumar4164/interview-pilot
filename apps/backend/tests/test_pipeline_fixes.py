"""Regression tests for real-time pipeline bugs, on both implementations.

Each test replays a scenario the way the goldens do (scripted LLM, real
Postgres checkpointer for the graph) and asserts the user-visible behavior:
what reaches the dashboard and what the pipeline remembers.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest

from src.config import settings
from src.graphs.checkpointer import Checkpointer
from src.graphs.code_graph import code_thread_id
from src.replay.runner import ReplayReport, ReplayRunner, analysis_worker_factory
from src.replay.scenario import load_scenario
from src.workers.langgraph_session import LangGraphSessionPipeline, thread_id_for

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(params=["worker", "graph"])
async def replay(request) -> AsyncIterator:
    """replay(scenario_file) -> ReplayReport, through one pipeline implementation."""
    if request.param == "worker":

        async def run_worker(name: str) -> ReplayReport:
            scenario = load_scenario(SCENARIOS_DIR / name)
            return await ReplayRunner(scenario, llm_mode="scripted", pipeline_factory=analysis_worker_factory).run()

        yield run_worker
        return

    if not _postgres_available():
        pytest.skip("Postgres not reachable")
    cp = Checkpointer()
    await cp.open(settings.database_url_sync)
    threads: list[str] = []

    def factory(session_id, fm, timing):
        return LangGraphSessionPipeline(
            session_id,
            fm,
            cp,
            min_analysis_interval_s=timing.min_analysis_interval_s,
            interim_throttle_s=timing.interim_throttle_s,
        )

    async def run_graph(name: str) -> ReplayReport:
        scenario = load_scenario(SCENARIOS_DIR / name)
        threads.extend((thread_id_for(scenario.meta.id), code_thread_id(scenario.meta.id)))
        await cp.delete_threads(*threads[-2:])
        return await ReplayRunner(scenario, llm_mode="scripted", pipeline_factory=factory).run()

    yield run_graph
    await cp.delete_threads(*threads)
    await cp.close()


def _contradiction_flags(report: ReplayReport) -> list[dict]:
    return [
        e.payload
        for e in report.events
        if e.method == "send_red_flag" and isinstance(e.payload, dict) and e.payload.get("type") == "contradiction"
    ]


async def test_a_statement_is_not_contradicted_by_its_own_facts(replay) -> None:
    """The scripted fact extractor returns sentences of the utterance, so a claim
    that is part of the flagged statement was extracted from that statement."""
    for name in ("03_contradiction_resume_vs_response.scenario.json", "04_long_interview_memory.scenario.json"):
        report = await replay(name)
        for flag in _contradiction_flags(report):
            assert flag["claim"] not in flag["new_statement"], f"{name}: statement flagged against itself: {flag}"


async def test_a_real_contradiction_is_still_flagged(replay) -> None:
    report = await replay("04_long_interview_memory.scenario.json")
    flags = _contradiction_flags(report)
    assert [(f["claim"][:40], f["new_statement"][:40]) for f in flags] == [
        ("I have used Kafka for about four years, ", "Actually I've never used Kafka Connect o")
    ]


INTERVIEWER_KAFKA = "On our team we used Kafka for years, so I'm curious how yours compared."


async def test_interviewer_statements_are_not_candidate_facts(replay) -> None:
    """Scenario 04's interviewer mentions their own team's Kafka use; the candidate
    later says they never used Kafka. Only the candidate's words are claims."""
    report = await replay("04_long_interview_memory.scenario.json")
    facts = report.final_state["memory"]["fact_store"]
    assert not any(
        INTERVIEWER_KAFKA in item for items in facts.values() if isinstance(items, list) for item in map(str, items)
    )
    assert all(f["claim"] != INTERVIEWER_KAFKA for f in _contradiction_flags(report))
    assert not [
        c for c in report.llm_calls if c["kind"] == "fact_extraction" and "(by interviewer)" in c["user_content"]
    ]


async def test_interim_transcripts_stay_out_of_memory(replay) -> None:
    """Interims are previews of a sentence that arrives again as a final; memory
    keeps finals only, so the 10-utterance window is not filled with fragments."""
    scenario = load_scenario(SCENARIOS_DIR / "05_interim_transcripts.scenario.json")
    finals = [e.text for e in scenario.events if getattr(e, "is_final", False)]
    interims = [e.text for e in scenario.events if getattr(e, "is_final", True) is False]
    assert interims, "scenario must contain interims"

    report = await replay("05_interim_transcripts.scenario.json")
    memory = report.final_state["memory"]
    assert [u["text"] for u in memory["working_memory"]] == finals
    assert memory["total_utterances"] == len(finals)
    for call in report.llm_calls:
        lines = call["user_content"].splitlines()
        assert not [i for i in interims if f"[candidate]: {i}" in lines or f"[interviewer]: {i}" in lines], call["kind"]
    # The dashboard still gets the interim previews.
    assert sum(1 for e in report.events if e.method == "send_transcript" and not e.payload["is_final"]) == len(interims)


# ── Session teardown ───────────────────────────────────────────────────────


@pytest.fixture
async def live_session(monkeypatch) -> AsyncIterator[str]:
    """A live session row, the app's DB dependency on a NullPool engine, no real Deepgram or report LLM."""
    import uuid as _uuid

    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from src.api import sessions as sessions_api
    from src.dependencies import get_db
    from src.main import app
    from src.models.session import InterviewSession
    from src.services.access_control import DEV_USER_ID
    from src.websocket import audio_handler

    if not _postgres_available():
        pytest.skip("Postgres not reachable")
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            yield session
            await session.commit()

    async def _no_report(**_kwargs) -> None:
        return None

    app.dependency_overrides[get_db] = _get_db
    monkeypatch.setattr(settings, "deepgram_api_key", "")  # MockDeepgramWorker
    monkeypatch.setattr(sessions_api, "_generate_report_background", _no_report)
    monkeypatch.setattr("src.websocket.auth.async_session_factory", factory)

    sid = _uuid.uuid4()
    async with factory() as db:
        db.add(
            InterviewSession(
                id=sid, user_id=DEV_USER_ID, status="live", level="sde2", duration_minutes=45, focus_areas=["coding"]
            )
        )
        await db.commit()
    yield str(sid)
    app.dependency_overrides.pop(get_db, None)
    audio_handler._active_sessions.pop(str(sid), None)
    audio_handler._active_workers.pop(str(sid), None)
    async with factory() as db:
        from src.models.report import Report
        from src.models.resilience import SessionSnapshot

        for model in (Report, SessionSnapshot):
            await db.execute(delete(model).where(model.session_id == sid))
        await db.execute(delete(InterviewSession).where(InterviewSession.id == sid))
        await db.commit()
    await engine.dispose()


@pytest.mark.parametrize("use_graph", [False, True], ids=["worker", "graph"])
async def test_ending_a_session_stops_its_transcription_and_pipeline(live_session: str, use_graph: bool) -> None:
    """Before the fix nothing removed an ended session's transcription service or
    pipeline: audio still arriving on the open socket kept producing transcripts
    and LLM analyses for a session that was over."""
    from unittest.mock import AsyncMock, patch

    import httpx

    from src.main import app
    from src.replay.runner import build_scripted_failover_manager
    from src.schemas.transcript import SpeakerRole, TranscriptEventSchema
    from src.services.transcription_service import TranscriptionService
    from src.websocket import audio_handler
    from src.websocket.manager import dashboard_manager
    from src.workers.analysis_worker import AnalysisWorker

    sid = live_session
    fm = build_scripted_failover_manager()
    cp = None
    if use_graph:
        cp = Checkpointer()
        await cp.open(settings.database_url_sync)
        pipeline = LangGraphSessionPipeline(sid, fm, cp, min_analysis_interval_s=0)
    else:
        pipeline = AnalysisWorker(sid, fm, min_analysis_interval_s=0)
    service = TranscriptionService(sid)  # registered the way get_transcription_service does
    service.on_event(pipeline.on_transcript_event)
    audio_handler._active_sessions[sid] = service
    audio_handler._active_workers[sid] = pipeline
    await service.start()

    def utterance(n: int) -> TranscriptEventSchema:
        return TranscriptEventSchema(
            id=f"{sid}-{n}",
            session_id=sid,
            speaker_role=SpeakerRole.CANDIDATE,
            text=_ANSWER,
            is_final=True,
            confidence=0.9,
            timestamp="2026-01-01T00:00:00+00:00",
            channel="microphone",
        )

    try:
        with (
            patch.object(dashboard_manager, "send_transcript", AsyncMock()),
            patch.object(dashboard_manager, "send_analysis", AsyncMock()) as send_analysis,
        ):
            await pipeline.on_transcript_event(utterance(0))
            await pipeline.on_code_snapshot("def two_sum(nums, target):\n    seen = {}\n", "python", "codemirror")
            await pipeline.wait_idle()
            assert send_analysis.await_count == 1

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(f"/api/sessions/{sid}/end")
            assert response.status_code == 200, response.text

            assert sid not in audio_handler._active_sessions and sid not in audio_handler._active_workers
            assert not service.is_running
            # More audio for the ended session (the extension is still streaming) does nothing.
            await pipeline.on_transcript_event(utterance(1))
            await asyncio.sleep(0.1)
            assert send_analysis.await_count == 1
        if cp is not None:
            state = await pipeline._compiled().aget_state({"configurable": {"thread_id": thread_id_for(sid)}})
            assert not state.values, "session checkpoints were not deleted"
            code_state = await pipeline._code.compiled().aget_state(
                {"configurable": {"thread_id": code_thread_id(sid)}}
            )
            assert not code_state.values, "code checkpoints were not deleted"
    finally:
        if service.is_running:
            await service.stop()
        if cp is not None:
            await cp.delete_threads(thread_id_for(sid), code_thread_id(sid))
            await cp.close()


_ANSWER = "I would keep a hash map from value to index and look up the complement in one pass."


def test_audio_socket_for_an_ended_session_is_refused(live_session: str) -> None:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from src.main import app

    with psycopg.connect(settings.database_url_sync) as conn:
        conn.execute("UPDATE interview_sessions SET status = 'ended' WHERE id = %s", (live_session,))
    try:
        with TestClient(app).websocket_connect(f"/ws/audio/{live_session}") as ws:
            ws.send_json({"type": "ping", "timestamp": 1})
            reply = ws.receive_json()
        pytest.fail(f"audio socket accepted for an ended session (replied {reply})")
    except WebSocketDisconnect as closed:
        assert closed.code == 1008 and closed.reason == "Session ended"
