"""Agentic report scoring (REPORT_AGENTIC_SCORING) through ReportRunner, real Postgres, scripted LLM."""

import asyncio
import re
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
from src.llm.failover import FailoverManager
from src.models.analysis import AnalysisEvent, CodeSnapshot
from src.models.plan import InterviewPlan
from src.models.report import Report
from src.models.session import InterviewSession
from src.models.transcript import Transcript
from src.replay import scripted_provider
from src.replay.runner import build_scripted_failover_manager
from src.replay.scripted_provider import LLMCallRecorder, recording
from src.services.access_control import DEV_USER_ID
from src.services.report_agentic import cost_units
from src.services.report_generator import ALL_CRITERIA


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")

TRANSCRIPT = [
    ("interviewer", "Tell me about running Kafka in production."),
    ("candidate", "I have used Kafka for about four years and built the consumer framework our teams use."),
    ("interviewer", "How did you size partitions?"),
    ("candidate", "I think partition count should follow target throughput divided by per-consumer capacity."),
    ("interviewer", "Let's code: return indices of two numbers adding up to a target."),
    ("candidate", "I'll keep a hash map from value to index and look up the complement in one pass."),
    ("candidate", "That makes it linear time and linear space, and I handle the empty input first."),
    ("interviewer", "What if the array is sorted?"),
    ("candidate", "Then two pointers from both ends work in constant space."),
    ("interviewer", "Tell me about a disagreement with a teammate."),
    ("candidate", "We disagreed on retries; I wrote a short doc comparing options and we picked idempotent consumers."),
    ("candidate", "Actually I've never used Kafka Connect myself, another team ran it."),
]


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
        for i, (who, text) in enumerate(TRANSCRIPT):
            db.add(
                Transcript(
                    session_id=sid,
                    speaker_role=who,
                    text=text,
                    is_final=True,
                    confidence=0.9,
                    timestamp=t0 + timedelta(seconds=40 * i),
                    channel="tab",
                )
            )
        for q in (4, 3, 5):
            db.add(AnalysisEvent(session_id=sid, answer_quality=q, on_track=True, insight=f"insight {q}"))
        db.add(
            CodeSnapshot(
                session_id=sid,
                code="def two_sum(nums, t): ...",
                language="python",
                timestamp=t0 + timedelta(minutes=4),
                approach="hash_map",
                progress_state="complete",
            )
        )
        await db.commit()
    reports: list[uuid.UUID] = []

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
        reports.append(rid)
        return str(rid)

    async def load(report_id: str) -> Report:
        async with factory() as db:
            return (await db.execute(select(Report).where(Report.id == uuid.UUID(report_id)))).scalar_one()

    yield {"sid": str(sid), "factory": factory, "checkpointer": checkpointer, "new_report": new_report, "load": load}

    await checkpointer.delete_threads(*(thread_id_for(str(r)) for r in reports))
    await checkpointer.close()
    async with factory() as db:
        for model in (Report, AnalysisEvent, CodeSnapshot, Transcript, InterviewPlan):
            await db.execute(delete(model).where(model.session_id == sid))
        await db.execute(delete(InterviewSession).where(InterviewSession.id == sid))
        await db.commit()
    await engine.dispose()


class _CountingFailover:
    """Wraps a FailoverManager to total the price (cost units) of every call it routes."""

    def __init__(self, inner: FailoverManager) -> None:
        self._inner = inner
        self.cost = 0.0
        self.tokens = 0

    async def analyze(self, context, *args, **kwargs):
        result = await self._inner.analyze(context, *args, **kwargs)
        self.cost += cost_units(context.task_type, result.input_tokens, result.output_tokens)
        self.tokens += result.input_tokens + result.output_tokens
        return result

    async def invoke_with_tools(self, task_type, *args, **kwargs):
        turn = await self._inner.invoke_with_tools(task_type, *args, **kwargs)
        self.cost += cost_units(
            task_type, turn.input_tokens, turn.output_tokens, turn.cache_read_tokens, turn.cache_write_tokens
        )
        self.tokens += turn.input_tokens + turn.output_tokens
        return turn


def _runner(env, fm, agentic: bool) -> ReportRunner:
    runner = ReportRunner(fm, env["factory"], env["checkpointer"])  # type: ignore[arg-type]
    runner._graph = rg.build_report_graph(fm, env["factory"], agentic=agentic)  # type: ignore[arg-type]
    return runner


ELAPSED = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] \"(.+?)\"")


async def test_agentic_report_scores_every_criterion_with_grounded_evidence(env) -> None:
    report_id = await env["new_report"]()
    recorder = LLMCallRecorder()
    with recording(recorder):
        await _runner(env, build_scripted_failover_manager(), agentic=True).run(report_id, env["sid"], {})
    report = await env["load"](report_id)
    assert report.status == "completed"
    data = report.report_data
    assert sorted(s["criterion"] for s in data["scores"]) == sorted(ALL_CRITERIA)
    elapsed_of = {f"00:{40 * i // 60:02d}:{40 * i % 60:02d}": text for i, (_, text) in enumerate(TRANSCRIPT)}
    for score in data["scores"]:
        assert 1 <= score["score"] <= 5 and score["evidence"], score
        for evidence in score["evidence"]:
            m = ELAPSED.match(evidence)
            assert m, evidence
            hh, mm, ss, quote = m.groups()
            assert quote in elapsed_of[f"{hh}:{mm}:{ss}"], evidence  # quote is from the entry at that time
    assert data["key_moments"]
    assert all(m["timestamp"] in elapsed_of for m in data["key_moments"])
    kinds = [c.kind for c in recorder.calls]
    assert kinds.count("report_synthesis") == 1 and "report_generation" not in kinds  # no fallback


async def test_agentic_report_costs_at_most_three_times_the_single_call_by_price(env) -> None:
    single = _CountingFailover(build_scripted_failover_manager())
    await _runner(env, single, agentic=False).run(await env["new_report"](), env["sid"], {})
    agentic = _CountingFailover(build_scripted_failover_manager())
    await _runner(env, agentic, agentic=True).run(await env["new_report"](), env["sid"], {})
    print(
        f"\ntokens: single-call {single.tokens}, agentic {agentic.tokens} ({agentic.tokens / single.tokens:.1f}x); "
        f"cost: single-call {single.cost:.0f}, agentic {agentic.cost:.0f} ({agentic.cost / single.cost:.2f}x)"
    )
    # Price-weighted (Sonnet criterion loops at REPORT_COST_SONNET_RATIO of an Opus token).
    assert 0 < agentic.cost <= 3 * single.cost


async def test_an_invalid_citation_is_repaired(env, monkeypatch) -> None:
    original = scripted_provider.TOOL_ANSWERS["report_criterion"]
    attempts: dict[str, int] = {}

    def first_reply_cites_nothing_real(system, messages, h):
        answer = original(system, messages, h)
        crit = re.search(r"Criterion: (\w+)", messages[0].content).group(1)  # type: ignore[union-attr]
        attempts[crit] = attempts.get(crit, 0) + 1
        if attempts[crit] == 1:
            return answer.replace('"entry_id": "T', '"entry_id": "T99')  # T99x does not exist
        return answer

    monkeypatch.setitem(scripted_provider.TOOL_ANSWERS, "report_criterion", first_reply_cites_nothing_real)
    report_id = await env["new_report"]()
    recorder = LLMCallRecorder()
    with recording(recorder):
        await _runner(env, build_scripted_failover_manager(), agentic=True).run(report_id, env["sid"], {})
    assert set(attempts.values()) == {2}  # each criterion repaired once
    assert "report_generation" not in [c.kind for c in recorder.calls]
    repair_turns = [
        c for c in recorder.calls if c.kind == "report_criterion" and "Fix these problems" in c.user_content
    ]
    assert len(repair_turns) == len(ALL_CRITERIA)
    assert (await env["load"](report_id)).status == "completed"


async def test_unrepairable_scoring_falls_back_to_the_single_call_report(env, monkeypatch) -> None:
    monkeypatch.setitem(scripted_provider.TOOL_ANSWERS, "report_criterion", lambda system, messages, h: "not json")
    report_id = await env["new_report"]()
    recorder = LLMCallRecorder()
    with recording(recorder):
        await _runner(env, build_scripted_failover_manager(), agentic=True).run(report_id, env["sid"], {})
    kinds = [c.kind for c in recorder.calls]
    assert "report_synthesis" not in kinds
    assert kinds.count("report_generation") == 1  # the single-call report was generated instead
    report = await env["load"](report_id)
    assert report.status == "completed" and len(report.report_data["scores"]) == len(ALL_CRITERIA)


async def test_resume_after_an_interruption_does_not_rescore_finished_criteria(env, monkeypatch) -> None:
    """Each criterion's result is checkpointed as it lands (durability=sync)."""
    stuck = "behavioral"
    original = rg.score_criterion
    release = asyncio.Event()

    async def one_hangs(fm, session_id, criterion, index):
        if criterion == stuck and not release.is_set():
            await asyncio.Event().wait()
        return await original(fm, session_id, criterion, index)

    monkeypatch.setattr(rg, "score_criterion", one_hangs)
    report_id = await env["new_report"]()
    runner = _runner(env, build_scripted_failover_manager(), agentic=True)
    first = LLMCallRecorder()
    with recording(first):
        task = asyncio.create_task(runner.run(report_id, env["sid"], {}))
        for _ in range(200):
            if (
                len(
                    {
                        c.user_content.split("Criterion: ")[1].split("\n")[0]
                        for c in first.calls
                        if c.kind == "report_criterion"
                    }
                )
                >= 6
            ):
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)  # let the six finish and checkpoint
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    release.set()
    second = LLMCallRecorder()
    with recording(second):
        assert await _runner(env, build_scripted_failover_manager(), agentic=True).resume(report_id)
    rescored = {
        c.user_content.split("Criterion: ")[1].split("\n")[0] for c in second.calls if c.kind == "report_criterion"
    }
    assert rescored == {stuck}
    assert (await env["load"](report_id)).status == "completed"
