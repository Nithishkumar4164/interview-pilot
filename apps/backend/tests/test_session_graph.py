"""LangGraph session pipeline: behavior the replay goldens cannot see.

The goldens (test_replay_golden.py) settle after every event, so each run is
one event. These tests cover what happens between events and across
processes, against the real Postgres checkpointer:

- rate limiting and batching in real time, compared with AnalysisWorker;
- latency of the analysis push, compared with AnalysisWorker;
- a compression in flight never delays an analysis;
- a new pipeline (process restart) continues from the checkpointed state;
- two pipelines on one session (two processes) never lose an update;
- a SIGKILL mid-run is finished on restart with no duplicate DB rows.
"""

import asyncio
import os
import signal
import statistics
import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import psycopg
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.graphs.checkpointer import Checkpointer
from src.models.analysis import AnalysisEvent
from src.models.session import InterviewSession
from src.models.transcript import Transcript
from src.replay.collector import EventCollector
from src.replay.runner import (
    REPLAY_LATENCY_MS,
    PipelineFactory,
    ReplayRunner,
    analysis_worker_factory,
    build_scripted_failover_manager,
)
from src.replay.scenario import QuestionChangeEvent, Scenario, ScenarioMeta, TranscriptEvent, load_scenario
from src.replay.scripted_provider import LLMCallRecorder, behavior, recording
from src.schemas.transcript import SpeakerRole, TranscriptEventSchema
from src.services.access_control import DEV_USER_ID
from src.services.persistence_service import PersistenceService
from src.websocket.manager import dashboard_manager
from src.workers.analysis_worker import AnalysisWorker
from src.workers.langgraph_session import LangGraphSessionPipeline, thread_id_for

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@pytest.fixture
async def checkpointer() -> AsyncIterator[Checkpointer]:
    cp = Checkpointer()
    await cp.open(settings.database_url_sync)
    yield cp
    await cp.close()


@pytest.fixture
async def thread(checkpointer: Checkpointer) -> AsyncIterator[str]:
    """A fresh session id whose checkpoint thread is removed afterwards."""
    session_id = f"test-session-{uuid.uuid4()}"
    yield session_id
    await checkpointer.delete_threads(thread_id_for(session_id))


def _graph_factory(checkpointer: Checkpointer) -> PipelineFactory:
    def factory(session_id, fm, min_interval_s):
        return LangGraphSessionPipeline(session_id, fm, checkpointer, min_analysis_interval_s=min_interval_s)

    return factory


def _c(text: str, delay_ms: int = 0) -> TranscriptEvent:
    return TranscriptEvent(speaker_role=SpeakerRole.CANDIDATE, text=text, delay_ms=delay_ms, channel="microphone")


def _i(text: str, delay_ms: int = 0) -> TranscriptEvent:
    return TranscriptEvent(speaker_role=SpeakerRole.INTERVIEWER, text=text, delay_ms=delay_ms)


def _answer(n: int) -> str:
    return f"Candidate answer number {n} talks through the approach step by step."


def _analyzed(report) -> list[str]:
    """The utterance each analysis was about, in order."""
    return [c["user_content"].rsplit("\n", 1)[-1] for c in report.llm_calls if c["kind"] == "utterance_analysis"]


# ── Rate limiting and batching, in real time ───────────────────────────────

INTERVAL_S = 0.5

TIMELINES = {
    # c1 and c2 land inside the window; only the latest (c2) is analyzed when it ends.
    "window-keeps-latest": [
        _c(_answer(0)),
        _c(_answer(1), 100),
        _c(_answer(2), 100),
        _i("Okay, and then?", 900),
        _c(_answer(3), 100),
        _i("Thanks.", 900),
    ],
    # A burst while the first analysis runs: only the latest of the rest is analyzed, when the window ends.
    "burst": [_c(_answer(0))] + [_c(_answer(n), 20) for n in range(1, 8)] + [_i("Let's move on.", 1200)],
}


@pytest.mark.parametrize("name", list(TIMELINES))
async def test_rate_limit_matches_analysis_worker(name: str, checkpointer: Checkpointer, thread: str) -> None:
    scenario = Scenario(meta=ScenarioMeta(id=thread), events=TIMELINES[name])
    reports = {}
    for impl, factory in (("worker", analysis_worker_factory), ("graph", _graph_factory(checkpointer))):
        reports[impl] = await ReplayRunner(
            scenario,
            llm_mode="scripted",
            real_time=True,
            pipeline_factory=factory,
            min_analysis_interval_s=INTERVAL_S,
        ).run()
        assert reports[impl].error is None

    assert _analyzed(reports["graph"]) == _analyzed(reports["worker"])
    assert len(_analyzed(reports["graph"])) < sum(e.speaker_role == SpeakerRole.CANDIDATE for e in scenario.events)
    # Memory saw every utterance either way.
    assert (
        reports["graph"].final_state["memory"]["total_utterances"]
        == reports["worker"].final_state["memory"]["total_utterances"]
        == len(scenario.events)
    )


async def test_same_tick_burst_is_rate_limited(checkpointer: Checkpointer, thread: str) -> None:
    """Deliberate difference from AnalysisWorker. It stamped the rate-limit clock
    when an analysis task *started*, so utterances delivered before the first
    task ran (same event-loop tick) all bypassed the window and were analyzed
    back to back. The ingress stamps it when it picks the target."""
    events = [_c(_answer(n)) for n in range(8)] + [_i("Let's move on.", 1200)]
    report = await ReplayRunner(
        Scenario(meta=ScenarioMeta(id=thread), events=events),
        llm_mode="scripted",
        real_time=True,
        pipeline_factory=_graph_factory(checkpointer),
        min_analysis_interval_s=INTERVAL_S,
    ).run()
    assert _analyzed(report) == [f"[candidate]: {_answer(n)}" for n in (0, 7)]
    assert report.final_state["memory"]["total_utterances"] == 9


async def test_pending_candidate_is_analyzed_against_its_own_question(checkpointer: Checkpointer, thread: str) -> None:
    """Deliberate difference from AnalysisWorker, which analyzed the waiting
    utterance later, against the *next* question, and delayed the new
    question's first answer behind it."""
    events = [
        QuestionChangeEvent(question_text="Question one", question_index=0),
        _c(_answer(0)),
        _c(_answer(1), 100),  # inside the window: waits
        QuestionChangeEvent(question_text="Question two", question_index=1, delay_ms=50),
        _c(_answer(2), 50),  # first answer to question two
        _i("Thanks.", 900),
    ]
    report = await ReplayRunner(
        Scenario(meta=ScenarioMeta(id=thread), events=events),
        llm_mode="scripted",
        real_time=True,
        pipeline_factory=_graph_factory(checkpointer),
        min_analysis_interval_s=INTERVAL_S,
    ).run()
    calls = [c["user_content"] for c in report.llm_calls if c["kind"] == "utterance_analysis"]
    assert [c.rsplit("\n", 1)[-1] for c in calls] == [f"[candidate]: {_answer(n)}" for n in (0, 1, 2)]
    assert "Question one" in calls[1] and "Question two" in calls[2]


# ── Latency ────────────────────────────────────────────────────────────────


async def _push_latencies_ms(pipeline, utterances: list[TranscriptEvent], settle: bool = True) -> list[float]:
    """Time from each final utterance to its analysis reaching the dashboard."""
    latencies: list[float] = []
    sent_at = 0.0

    async def on_analysis(session_id, payload) -> None:
        latencies.append((time.perf_counter() - sent_at) * 1000)

    async with EventCollector():
        with patch.object(dashboard_manager, "send_analysis", on_analysis):
            for n, ev in enumerate(utterances):
                sent_at = time.perf_counter()
                await pipeline.on_transcript_event(
                    TranscriptEventSchema(
                        id=f"{pipeline.session_id}-{n}",
                        session_id=pipeline.session_id,
                        speaker_role=ev.speaker_role,
                        text=ev.text,
                        is_final=True,
                        confidence=0.9,
                        timestamp="2026-01-01T00:00:00+00:00",
                        channel=ev.channel,
                    )
                )
                if settle:
                    await pipeline.wait_idle()
            await pipeline.wait_idle()
    return latencies


async def test_analysis_push_latency_is_within_budget(checkpointer: Checkpointer, thread: str) -> None:
    """Budget: median at most 15 ms over AnalysisWorker on the same scripted latencies.

    The graph adds a superstep before the analysis node and a checkpoint per
    step; against a real provider (0.5-3 s per analysis) this is noise, but
    a regression in the hot path (e.g. a node that blocks on I/O) shows here.
    """
    scenario = load_scenario(SCENARIOS_DIR / "04_long_interview_memory.scenario.json")
    utterances = [e for e in scenario.events if isinstance(e, TranscriptEvent)]
    fm = build_scripted_failover_manager()
    with behavior(REPLAY_LATENCY_MS):
        old = await _push_latencies_ms(AnalysisWorker(f"{thread}-old", fm, min_analysis_interval_s=0), utterances)
        new = await _push_latencies_ms(
            LangGraphSessionPipeline(thread, fm, checkpointer, min_analysis_interval_s=0), utterances
        )
    print(
        f"\nanalysis push ms: worker median {statistics.median(old):.1f} max {max(old):.1f}; "
        f"graph median {statistics.median(new):.1f} max {max(new):.1f}"
    )
    assert len(new) == len(old) > 10
    assert statistics.median(new) <= statistics.median(old) + 15


async def test_compression_in_flight_does_not_delay_analysis(checkpointer: Checkpointer, thread: str) -> None:
    fm = build_scripted_failover_manager()
    pipeline = LangGraphSessionPipeline(thread, fm, checkpointer, min_analysis_interval_s=0)
    filler = [_i(f"Short interviewer remark {n}.") for n in range(19)]
    with behavior({**REPLAY_LATENCY_MS, "summary": 1000}):
        await _push_latencies_ms(pipeline, filler)
        # The 20th buffered utterance triggers a one-second compression; the
        # next answer must not wait for it.
        started = time.perf_counter()
        latencies = await _push_latencies_ms(pipeline, [_i("Twentieth remark."), _c(_answer(1))], settle=False)
        elapsed = time.perf_counter() - started
    assert len(latencies) == 1
    assert latencies[0] < 300, f"analysis waited on compression: {latencies[0]:.0f} ms"
    assert elapsed >= 1.0  # wait_idle did wait for the compression itself
    assert pipeline.to_dict()["memory"]["rolling_summary"].startswith("scripted-summary-")


# ── Durability across pipelines and processes ──────────────────────────────

CLAIM = (
    "I have used Kafka for about four years on the ingestion side and I built the consumer framework "
    "our teams still use for exactly-once processing."
)
DENIAL = "Actually I've never used Kafka myself, another team ran all of that for us."


async def test_new_pipeline_continues_from_checkpointed_state(checkpointer: Checkpointer, thread: str) -> None:
    """What a backend restart looks like to a session: memory, facts and question context survive."""
    fm = build_scripted_failover_manager()
    first = LangGraphSessionPipeline(thread, fm, checkpointer, min_analysis_interval_s=0)
    async with EventCollector():
        await first.on_question_change("Tell me about Kafka.", {"id": "q-kafka"}, 2)
        await _push_latencies_ms(first, [_c(CLAIM)])
    before = first.to_dict()

    # The scripted LLM's recorder is a contextvar: start the pipeline (its consumer task) inside it.
    recorder = LLMCallRecorder()
    with recording(recorder):
        second = LangGraphSessionPipeline(thread, fm, checkpointer, min_analysis_interval_s=0)
        # Reads served before the restarted pipeline's first run see the checkpointed state.
        await second.wait_idle()
        assert second.to_dict() == before
        hint = await second.handle_runtime_hint_request({"question_id": "q-kafka"})
        assert hint["generation_number"] == 1
        assert f"[candidate]: {CLAIM}" in recorder.calls[0].user_content

        await _push_latencies_ms(second, [_c(DENIAL)])
    after = second.to_dict()

    prompt = next(c.user_content for c in recorder.calls if c.kind == "utterance_analysis")
    assert "## Current Question\nTell me about Kafka." in prompt
    assert f"Experience: {CLAIM}" in prompt  # the fact store came back
    assert f"[candidate]: {CLAIM}" in prompt  # and so did working memory
    assert after["memory"]["fact_store"] == before["memory"]["fact_store"]
    assert after["analyses_run"] == before["analyses_run"] + 1
    # The denial contradicts a claim made before the restart.
    new = after["contradictions"]["contradictions"][len(before["contradictions"]["contradictions"]) :]
    assert [(c["claim"], c["new_statement"], c["detection_method"]) for c in new] == [(CLAIM, DENIAL, "rule_based")]


async def test_two_pipelines_on_one_session_lose_no_updates(checkpointer: Checkpointer, thread: str) -> None:
    """Two processes holding a pipeline for one session (an audio reconnect that
    landed on the other worker) take turns; neither overwrites the other's runs."""
    fm = build_scripted_failover_manager()
    a = LangGraphSessionPipeline(thread, fm, checkpointer, min_analysis_interval_s=0)
    b = LangGraphSessionPipeline(thread, fm, checkpointer, min_analysis_interval_s=0)
    with behavior(REPLAY_LATENCY_MS):
        async with EventCollector():
            await asyncio.gather(
                *(
                    p.on_transcript_event(
                        TranscriptEventSchema(
                            id=f"{name}-{n}",
                            session_id=thread,
                            speaker_role=SpeakerRole.CANDIDATE,
                            text=_answer(n),
                            is_final=True,
                            confidence=0.9,
                            timestamp="2026-01-01T00:00:00+00:00",
                            channel="microphone",
                        )
                    )
                    for n in range(5)
                    for name, p in (("a", a), ("b", b))
                )
            )
            await asyncio.gather(a.wait_idle(), b.wait_idle())

    state = await a._compiled().aget_state({"configurable": {"thread_id": thread_id_for(thread)}})
    assert state.values["total_utterances"] == 10
    assert state.values["analysis_count"] == 10


_CHILD = """
import asyncio, sys
from unittest.mock import patch
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from src.config import settings
from src.graphs import session_graph
from src.graphs.checkpointer import Checkpointer
from src.replay.runner import build_scripted_failover_manager
from src.schemas.transcript import SpeakerRole, TranscriptEventSchema
from src.services.persistence_service import PersistenceService
from src.workers.langgraph_session import LangGraphSessionPipeline

async def main(session_id, durability, texts):
    factory = async_sessionmaker(create_async_engine(settings.database_url, poolclass=NullPool), expire_on_commit=False)
    persistence = PersistenceService(factory)
    original = session_graph.check_contradictions
    calls = 0

    async def check_then_hang(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            await persistence._flush_batch()  # the analysis row is in the DB before the kill
            print("IN_FOLLOW_UP", flush=True)
            await asyncio.Event().wait()
        return await original(*args, **kwargs)

    session_graph.check_contradictions = check_then_hang
    cp = Checkpointer(); await cp.open(settings.database_url_sync, max_size=2)
    with patch("src.dependencies.get_persistence_service", lambda: persistence):
        p = LangGraphSessionPipeline(session_id, build_scripted_failover_manager(), cp,
                                     min_analysis_interval_s=0, durability=durability)
        for n, text in enumerate(texts):
            await p.on_transcript_event(TranscriptEventSchema(
                id=f"utt-{n}", session_id=session_id, speaker_role=SpeakerRole.CANDIDATE, text=text,
                is_final=True, confidence=0.9, timestamp="2026-01-01T00:00:00+00:00", channel="microphone"))
            await p.wait_idle()

asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
"""


@pytest.fixture
async def db_session() -> AsyncIterator[tuple[str, async_sessionmaker]]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = uuid.uuid4()
    async with factory() as db:
        db.add(
            InterviewSession(
                id=sid, user_id=DEV_USER_ID, status="active", level="sde2", duration_minutes=45, focus_areas=["coding"]
            )
        )
        await db.commit()
    yield str(sid), factory
    async with factory() as db:
        for model in (AnalysisEvent, Transcript):
            await db.execute(delete(model).where(model.session_id == sid))
        await db.execute(delete(InterviewSession).where(InterviewSession.id == sid))
        await db.commit()
    await engine.dispose()


@pytest.mark.parametrize("durability", ["sync", "async"])
async def test_sigkill_mid_run_is_finished_on_restart_without_duplicates(
    durability: str, checkpointer: Checkpointer, db_session
) -> None:
    session_id, factory = db_session
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CHILD,
        session_id,
        durability,
        CLAIM,
        DENIAL,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ, "PYTHONPATH": "."},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def wait_for_marker() -> None:
        while (line := await child.stdout.readline()) and line.strip() != b"IN_FOLLOW_UP":
            pass
        assert line, "child exited before reaching the contradiction check"

    try:
        await asyncio.wait_for(wait_for_marker(), timeout=60)
    finally:
        if child.returncode is None:
            child.send_signal(signal.SIGKILL)
        await child.wait()
    assert child.returncode == -signal.SIGKILL

    try:
        persistence = PersistenceService(factory)
        sent: list[tuple[str, dict]] = []

        def capture(method):
            async def _send(_sid, payload):
                sent.append((method, payload))

            return _send

        with (
            patch("src.dependencies.get_persistence_service", lambda: persistence),
            patch.object(dashboard_manager, "send_red_flag", capture("send_red_flag")),
            patch.object(dashboard_manager, "send_analysis", capture("send_analysis")),
            patch.object(dashboard_manager, "send_hint", capture("send_hint")),
        ):
            restarted = LangGraphSessionPipeline(
                session_id, build_scripted_failover_manager(), checkpointer, durability=durability
            )
            await restarted.wait_idle()  # loading the pipeline finishes the interrupted run
            await persistence._flush_batch()

        state = await restarted._compiled().aget_state({"configurable": {"thread_id": thread_id_for(session_id)}})
        assert state.next == ()
        values = restarted.to_dict()
        assert values["analyses_run"] == 2
        assert [u["text"] for u in values["memory"]["working_memory"]] == [CLAIM, DENIAL]
        flags = [p for m, p in sent if m == "send_red_flag" and p.get("type") == "contradiction"]
        assert len(flags) == 1 and flags[0]["new_statement"] == DENIAL

        async with factory() as db:
            rows = (
                await db.execute(
                    select(AnalysisEvent.idempotency_key, func.count())
                    .where(AnalysisEvent.session_id == uuid.UUID(session_id))
                    .group_by(AnalysisEvent.idempotency_key)
                )
            ).all()
        assert sorted(rows) == [(f"{session_id}:utt-0:analysis", 1), (f"{session_id}:utt-1:analysis", 1)]
        if durability == "sync":
            # The analyze step was checkpointed before the kill, so it is not redone.
            assert not [m for m, _ in sent if m == "send_analysis"]
    finally:
        await checkpointer.delete_threads(thread_id_for(session_id))
