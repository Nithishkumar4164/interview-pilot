"""Smoke-level regression coverage for the transcript-level replay harness."""

from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest

from src.config import settings
from src.graphs.checkpointer import Checkpointer
from src.graphs.code_graph import code_thread_id
from src.replay.runner import ReplayRunner, graph_pipeline_factory
from src.replay.scenario import load_scenario
from src.workers.langgraph_session import thread_id_for

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def _scenario_paths() -> list[Path]:
    return sorted(SCENARIOS_DIR.glob("*.scenario.json"))


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


async def _replay(scenario, checkpointer: Checkpointer):
    threads = (thread_id_for(scenario.meta.id), code_thread_id(scenario.meta.id))
    await checkpointer.delete_threads(*threads)
    try:
        return await ReplayRunner(
            scenario, llm_mode="mock", pipeline_factory=graph_pipeline_factory(checkpointer)
        ).run()
    finally:
        await checkpointer.delete_threads(*threads)


# Per-scenario invariants checked under llm_mode="mock". Mock LLM responses
# don't include 6-dim or contradiction signals, so assertions stay coarse —
# the goal here is to catch wiring regressions, not analysis quality.
EXPECTED = {
    "01-strong-candidate-two-sum": {
        "min_transcripts": 4,
        "min_analyses": 1,
        "min_code_ratings": 1,
    },
    "02-stuck-candidate-needs-hints": {
        "min_transcripts": 4,
        "min_analyses": 1,
        "min_hint_responses": 3,
        "min_code_ratings": 1,
    },
    "03-contradiction-resume-vs-response": {
        "min_transcripts": 4,
        "min_analyses": 1,
    },
    "04-long-interview-memory": {
        "min_transcripts": 28,
        "min_analyses": 10,
        "min_hint_responses": 2,
    },
    "05-interim-transcripts": {
        "min_transcripts": 4,
        "min_analyses": 2,
    },
    # Failure markers only affect the scripted LLM; under mock it is a normal run.
    "07-live-coding": {
        "min_transcripts": 4,
        "min_analyses": 3,
        "min_code_ratings": 1,
    },
    "06-llm-failures": {
        "min_transcripts": 4,
        "min_analyses": 3,
    },
}


@pytest.mark.parametrize(
    "scenario_path",
    _scenario_paths(),
    ids=lambda p: p.stem,
)
@pytest.mark.asyncio
async def test_scenario_replay_smoke(scenario_path: Path, checkpointer: Checkpointer) -> None:
    scenario = load_scenario(scenario_path)
    report = await _replay(scenario, checkpointer)

    assert report.error is None, f"Replay raised: {report.error}"

    invariants = EXPECTED.get(scenario.meta.id)
    assert invariants is not None, (
        f"No invariants defined for scenario id {scenario.meta.id!r}; add an entry to EXPECTED."
    )

    if "min_transcripts" in invariants:
        assert report.summary.transcripts >= invariants["min_transcripts"]
    if "min_analyses" in invariants:
        assert report.summary.analyses >= invariants["min_analyses"]
    if "min_hint_responses" in invariants:
        assert len(report.hint_responses) >= invariants["min_hint_responses"]
    if "min_code_ratings" in invariants:
        assert len(report.code_ratings) >= invariants["min_code_ratings"]


@pytest.mark.asyncio
async def test_malformed_scenario_raises_validation_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.scenario.json"
    bad.write_text('{"meta": {"id": "bad"}, "events": [{"type": "transcript"}]}')
    with pytest.raises(Exception):
        load_scenario(bad)


@pytest.mark.asyncio
async def test_replay_is_deterministic_under_mock_llm(checkpointer: Checkpointer) -> None:
    scenario = load_scenario(SCENARIOS_DIR / "01_strong_candidate_two_sum.scenario.json")
    r1 = await _replay(scenario, checkpointer)
    r2 = await _replay(scenario, checkpointer)
    assert r1.summary == r2.summary
    assert len(r1.events) == len(r2.events)
    assert [e.method for e in r1.events] == [e.method for e in r2.events]
