"""Regression tests for real-time pipeline bugs, on both implementations.

Each test replays a scenario the way the goldens do (scripted LLM, real
Postgres checkpointer for the graph) and asserts the user-visible behavior:
what reaches the dashboard and what the pipeline remembers.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest

from src.config import settings
from src.graphs.checkpointer import Checkpointer
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
        threads.append(thread_id_for(scenario.meta.id))
        await cp.delete_threads(threads[-1])
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
