"""ReportGraph against a real Postgres (checkpointer + app tables) with the scripted LLM."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.graphs import report_graph as rg
from src.graphs.checkpointer import Checkpointer
from src.graphs.report_graph import ReportRunner, thread_id_for
from src.models.analysis import AnalysisEvent
from src.models.plan import InterviewPlan
from src.models.report import Report
from src.models.session import InterviewSession
from src.models.transcript import Transcript
from src.replay.runner import build_scripted_failover_manager
from src.replay.scripted_provider import LLMCallRecorder, recording
from src.services.access_control import DEV_USER_ID
from src.services.report_generator import ReportGenerator
from src.services.report_service import ReportService


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")

SNAPSHOTS = {
    "duration_minutes": 42.0,
    "target_level": "sde2",
    "fact_store": {"claimed_experience": ["5 years of Kafka"], "key_numbers": ["1B events/day"]},
    "rolling_summary": "Candidate discussed Kafka and solved two-sum with a hash map.",
    "analysis_results": [
        {"answer_quality": 4, "on_track": True, "insight": "Clear", "red_flags": [], "hint_signal": "none"},
        {"answer_quality": 2, "on_track": False, "insight": "Vague", "red_flags": ["vague"], "hint_signal": "nudge"},
    ],
    "contradictions": [{"explanation": "Claimed Kafka lead, unsure about partitions", "confidence": 0.9}],
    "code_progress": "complete",
}


@pytest.fixture
async def env():
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    checkpointer = Checkpointer()
    await checkpointer.open(settings.database_url_sync, max_size=4)

    sid = uuid.uuid4()
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    async with factory() as db:
        db.add(
            InterviewSession(
                id=sid, user_id=DEV_USER_ID, status="ended", level="sde2", duration_minutes=45, focus_areas=["coding"]
            )
        )
        await db.flush()
        db.add(
            InterviewPlan(
                session_id=sid,
                version=1,
                is_active=True,
                plan_data={"phases": [{"phase": "coding", "questions": [{"id": "q1", "text": "Two sum"}]}]},
            )
        )
        for i, (who, text) in enumerate(
            [("interviewer", "Solve two sum."), ("candidate", "I'd use a hash map from value to index.")]
        ):
            db.add(
                Transcript(
                    session_id=sid,
                    speaker_role=who,
                    text=text,
                    is_final=True,
                    confidence=0.9,
                    timestamp=t0 + timedelta(minutes=5 * i),
                    channel="tab",
                )
            )
        db.add(AnalysisEvent(session_id=sid, answer_quality=4, on_track=True, insight="Good approach"))
        await db.commit()

    created_reports: list[uuid.UUID] = []

    async def new_report() -> str:
        rid = uuid.uuid4()
        async with factory() as db:
            db.add(
                Report(
                    id=rid,
                    session_id=sid,
                    report_data={},
                    recommendation="lean_hire",
                    confidence_level=0.0,
                    is_final=False,
                    status="pending",
                )
            )
            await db.commit()
        created_reports.append(rid)
        return str(rid)

    async def load_report(report_id: str) -> Report:
        async with factory() as db:
            return (await db.execute(select(Report).where(Report.id == uuid.UUID(report_id)))).scalar_one()

    yield {
        "sid": str(sid),
        "factory": factory,
        "checkpointer": checkpointer,
        "new_report": new_report,
        "load_report": load_report,
    }

    await checkpointer.delete_threads(*(thread_id_for(str(r)) for r in created_reports))
    await checkpointer.close()
    async with factory() as db:
        for model in (Report, AnalysisEvent, Transcript, InterviewPlan):
            await db.execute(delete(model).where(model.session_id == sid))
        await db.execute(delete(InterviewSession).where(InterviewSession.id == sid))
        await db.commit()
    await engine.dispose()


def _runner(env) -> ReportRunner:
    return ReportRunner(build_scripted_failover_manager(), env["factory"], env["checkpointer"])


def _comparable(report_data: dict) -> dict:
    return {k: v for k, v in report_data.items() if k != "generated_at"}


async def test_graph_matches_the_original_pipeline(env) -> None:
    """Same inputs, same scripted LLM: the graph persists exactly what the old path produced."""
    fm = build_scripted_failover_manager()
    async with env["factory"]() as db:
        report_input = await ReportService(fm).load_inputs(env["sid"], SNAPSHOTS, db)
    expected = await ReportGenerator(fm).generate(report_input)

    report_id = await env["new_report"]()
    await _runner(env).run(report_id, env["sid"], SNAPSHOTS)

    row = await env["load_report"](report_id)
    assert row.status == "completed" and row.is_final
    assert row.recommendation == expected.recommendation.value
    assert row.confidence_level == expected.confidence_level
    assert _comparable(row.report_data) == _comparable(
        {
            "scores": [s.model_dump() for s in expected.scores],
            "key_moments": [m.model_dump() for m in expected.key_moments],
            "strengths": expected.strengths,
            "areas_for_growth": expected.areas_for_growth,
            "level_comparison": expected.level_comparison,
            "hint_summary": expected.hint_summary,
            "annotated_transcript": expected.annotated_transcript,
            "generated_at": expected.generated_at,
        }
    )
    assert len(row.report_data["annotated_transcript"]) == 2  # DB transcripts were loaded


async def test_crash_after_llm_call_resumes_without_calling_the_llm_again(env, monkeypatch) -> None:
    """Shutdown cancels the task awaiting the graph while it is persisting; a restart finishes the report."""
    report_id = await env["new_report"]()
    real_persist = rg.persist_report
    reached_persist = asyncio.Event()

    async def hangs(*_args, **_kwargs):
        reached_persist.set()
        await asyncio.Event().wait()  # never returns; the task is cancelled here

    monkeypatch.setattr(rg, "persist_report", hangs)
    recorder = LLMCallRecorder()
    with recording(recorder):
        task = asyncio.create_task(_runner(env).run(report_id, env["sid"], SNAPSHOTS))
        await asyncio.wait_for(reached_persist.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert (await env["load_report"](report_id)).status == "pending", "a cancelled run must not be marked failed"
    assert [c.kind for c in recorder.calls] == ["report_generation"]

    # "Restart": new checkpointer connection and runner, as a fresh process would have.
    monkeypatch.setattr(rg, "persist_report", real_persist)
    fresh = Checkpointer()
    await fresh.open(settings.database_url_sync, max_size=2)
    try:
        with recording(recorder):
            resumed = await ReportRunner(build_scripted_failover_manager(), env["factory"], fresh).resume_pending()
    finally:
        await fresh.close()

    assert report_id in resumed
    row = await env["load_report"](report_id)
    assert row.status == "completed" and row.report_data["scores"]
    assert [c.kind for c in recorder.calls] == ["report_generation"], "LLM was called again on resume"
    state = await env["checkpointer"].saver.aget_tuple({"configurable": {"thread_id": thread_id_for(report_id)}})
    assert state is None, "thread should be deleted after the run"


async def test_concurrent_runs_of_one_report_call_the_llm_once(env, monkeypatch) -> None:
    report_id = await env["new_report"]()
    monkeypatch.setattr(settings, "mock_llm_latency_ms", 0)
    recorder = LLMCallRecorder()
    runner_a, runner_b = _runner(env), _runner(env)

    # Slow the LLM so both runs overlap.
    original = ReportGenerator.call_llm

    async def slow_call(self, report_input):
        await asyncio.sleep(0.5)
        return await original(self, report_input)

    monkeypatch.setattr(ReportGenerator, "call_llm", slow_call)
    with recording(recorder):
        await asyncio.gather(
            runner_a.run(report_id, env["sid"], SNAPSHOTS),
            runner_b.run(report_id, env["sid"], SNAPSHOTS),
        )

    assert [c.kind for c in recorder.calls] == ["report_generation"]
    assert (await env["load_report"](report_id)).status == "completed"


async def test_failure_marks_the_report_failed_and_cleans_up(env, monkeypatch) -> None:
    report_id = await env["new_report"]()

    async def broken_load(*_args, **_kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(ReportService, "load_inputs", broken_load)
    await _runner(env).run(report_id, env["sid"], SNAPSHOTS)  # must not raise

    assert (await env["load_report"](report_id)).status == "failed"
    state = await env["checkpointer"].saver.aget_tuple({"configurable": {"thread_id": thread_id_for(report_id)}})
    assert state is None


async def test_llm_failure_still_produces_the_fallback_report(env, monkeypatch) -> None:
    report_id = await env["new_report"]()

    async def provider_down(self, report_input):
        return {"error": "all providers down"}

    monkeypatch.setattr(ReportGenerator, "call_llm", provider_down)
    await _runner(env).run(report_id, env["sid"], SNAPSHOTS)

    row = await env["load_report"](report_id)
    assert row.status == "completed"
    assert row.confidence_level == 0.3  # _fallback_report
    assert "Full LLM analysis was unavailable" in row.report_data["areas_for_growth"]


async def test_no_db_session_is_open_during_the_llm_call(env, monkeypatch) -> None:
    """A slow or failing-over provider must not hold a pooled DB connection."""
    open_sessions = 0
    real_factory = env["factory"]

    class Tracked:
        async def __aenter__(self):
            nonlocal open_sessions
            open_sessions += 1
            self._cm = real_factory()
            return await self._cm.__aenter__()

        async def __aexit__(self, *exc):
            nonlocal open_sessions
            open_sessions -= 1
            return await self._cm.__aexit__(*exc)

    seen_during_llm: list[int] = []
    original = ReportGenerator.call_llm

    async def observing(self, report_input):
        seen_during_llm.append(open_sessions)
        return await original(self, report_input)

    monkeypatch.setattr(ReportGenerator, "call_llm", observing)
    report_id = await env["new_report"]()
    runner = ReportRunner(build_scripted_failover_manager(), lambda: Tracked(), env["checkpointer"])
    await runner.run(report_id, env["sid"], SNAPSHOTS)

    assert seen_during_llm == [0]
    assert (await env["load_report"](report_id)).status == "completed"


_CHILD = """
import asyncio, sys
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from src.config import settings
from src.graphs import report_graph as rg
from src.graphs.checkpointer import Checkpointer
from src.replay.runner import build_scripted_failover_manager

async def main(report_id, session_id):
    async def hang(*_a, **_k):
        print("PERSISTING", flush=True)
        await asyncio.Event().wait()
    rg.persist_report = hang
    cp = Checkpointer(); await cp.open(settings.database_url_sync, max_size=2)
    factory = async_sessionmaker(create_async_engine(settings.database_url, poolclass=NullPool), expire_on_commit=False)
    await rg.ReportRunner(build_scripted_failover_manager(), factory, cp).run(report_id, session_id, {})

asyncio.run(main(sys.argv[1], sys.argv[2]))
"""


async def test_sigkill_mid_run_leaves_completed_steps_checkpointed(env) -> None:
    """A hard kill runs no cleanup code: only per-step (sync) checkpoints survive it."""
    import os
    import signal
    import sys
    from pathlib import Path

    report_id = await env["new_report"]()
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CHILD,
        report_id,
        env["sid"],
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ, "PYTHONPATH": "."},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def wait_for_marker() -> None:
        # stdout also carries the child's log lines; skip to the marker.
        while (line := await child.stdout.readline()) and line.strip() != b"PERSISTING":
            pass
        assert line, "child exited before reaching persist"

    try:
        await asyncio.wait_for(wait_for_marker(), timeout=60)
    finally:
        if child.returncode is None:
            child.send_signal(signal.SIGKILL)
        await child.wait()
    assert child.returncode == -signal.SIGKILL

    runner = _runner(env)
    state = await runner._app().aget_state({"configurable": {"thread_id": thread_id_for(report_id)}})
    assert state.next == ("persist",), f"expected generate+assemble checkpointed, got next={state.next}"
    assert "report" in state.values  # the LLM result is already on disk

    recorder = LLMCallRecorder()
    with recording(recorder):
        assert await runner.resume(report_id)
    assert recorder.calls == [], "resume after SIGKILL called the LLM again"
    assert (await env["load_report"](report_id)).status == "completed"
