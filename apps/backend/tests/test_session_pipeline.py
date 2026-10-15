"""The SessionPipeline seam: conformance and the reads handlers depend on."""

from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest

from src.config import settings
from src.graphs.checkpointer import Checkpointer
from src.graphs.code_graph import code_thread_id
from src.replay.runner import PipelineTiming, ReplayRunner, build_scripted_failover_manager
from src.replay.scenario import load_scenario
from src.workers.langgraph_session import LangGraphSessionPipeline, thread_id_for
from src.workers.session_pipeline import SessionPipeline, create_session_pipeline

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


@pytest.fixture
async def checkpointer() -> AsyncIterator[Checkpointer]:
    if not _postgres_available():
        pytest.skip("Postgres not reachable")
    cp = Checkpointer()
    await cp.open(settings.database_url_sync)
    yield cp
    await cp.close()


def test_factory_returns_the_langgraph_pipeline() -> None:
    pipeline = create_session_pipeline("s-1", build_scripted_failover_manager())
    assert isinstance(pipeline, LangGraphSessionPipeline)
    assert isinstance(pipeline, SessionPipeline)


async def test_report_snapshot_reflects_the_session(checkpointer: Checkpointer) -> None:
    """End-session builds report inputs from report_snapshot(); check it carries real session data."""
    captured: dict[str, LangGraphSessionPipeline] = {}

    def factory(session_id, fm, timing: PipelineTiming):
        captured["pipeline"] = LangGraphSessionPipeline(
            session_id, fm, checkpointer, min_analysis_interval_s=timing.min_analysis_interval_s
        )
        return captured["pipeline"]

    scenario = load_scenario(SCENARIOS_DIR / "04_long_interview_memory.scenario.json")
    threads = (thread_id_for(scenario.meta.id), code_thread_id(scenario.meta.id))
    await checkpointer.delete_threads(*threads)
    try:
        report = await ReplayRunner(scenario, llm_mode="scripted", pipeline_factory=factory).run()
    finally:
        await checkpointer.delete_threads(*threads)
    assert report.error is None

    snap = captured["pipeline"].report_snapshot()
    assert set(snap) == {
        "fact_store",
        "rolling_summary",
        "analysis_results",
        "contradictions",
        "code_progress",
        "detected_approach",
        "copilot_cards_used",
        "scorecard_notes",
    }
    assert len(snap["analysis_results"]) == report.summary.analyses
    assert len(snap["contradictions"]) == report.summary.contradictions >= 1
    assert snap["fact_store"]["claimed_experience"]


def test_get_transcripts_by_ids_returns_matching_finals_in_order() -> None:
    pipeline = create_session_pipeline("s-2", build_scripted_failover_manager())
    assert isinstance(pipeline, LangGraphSessionPipeline)
    pipeline._transcript_history.extend({"id": i, "text": f"t{i}"} for i in ("a", "b", "c"))
    assert [t["id"] for t in pipeline.get_transcripts_by_ids({"c", "a"})] == ["a", "c"]
    assert pipeline.get_transcripts_by_ids(set()) == []
