"""Golden replay of the real-time pipeline under the scripted LLM.

The goldens were recorded from the pre-LangGraph AnalysisWorker (with
realistic call latencies, settling after each event) and the LangGraph
session pipeline reproduced them before AnalysisWorker was removed. Later
intentional behavior changes re-recorded them, each with a reviewed diff
(see git log tests/golden). They are the oracle for the pipeline's dashboard
pushes, DB writes, LLM prompts and state.

The session_state section was re-recorded from the graph when
SessionStateService was removed: it is the graph's current snapshot, which
Phase 4 showed equals SessionStateService's snapshot refreshed at the end.

To re-record after an intentional behavior change:
    REPLAY_UPDATE_GOLDEN=1 uv run pytest tests/test_replay_golden.py
and review the diff before committing.
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest

from src.config import settings
from src.graphs.checkpointer import Checkpointer
from src.graphs.code_graph import code_thread_id
from src.replay.golden import golden_path, normalize, read_golden, write_golden
from src.replay.runner import ReplayRunner, graph_pipeline_factory
from src.replay.scenario import load_scenario
from src.workers.langgraph_session import thread_id_for

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
GOLDEN_DIR = Path(__file__).parent / "golden"
UPDATE = os.environ.get("REPLAY_UPDATE_GOLDEN") == "1"


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


async def _run(path: Path, checkpointer: Checkpointer) -> dict:
    scenario = load_scenario(path)
    threads = (thread_id_for(scenario.meta.id), code_thread_id(scenario.meta.id))
    await checkpointer.delete_threads(*threads)
    try:
        report = await ReplayRunner(
            scenario, llm_mode="scripted", pipeline_factory=graph_pipeline_factory(checkpointer)
        ).run()
        return normalize(report)
    finally:
        await checkpointer.delete_threads(*threads)


@pytest.mark.parametrize("scenario_path", _scenario_paths(), ids=lambda p: p.stem)
async def test_replay_matches_golden(scenario_path: Path, checkpointer: Checkpointer) -> None:
    actual = await _run(scenario_path, checkpointer)
    path = golden_path(GOLDEN_DIR, actual["scenario_id"])

    if UPDATE:
        write_golden(path, actual)
        pytest.skip(f"golden re-recorded: {path.name}")

    assert path.exists(), f"missing golden {path}; record with REPLAY_UPDATE_GOLDEN=1"
    expected = read_golden(path)
    # Compare section by section so a failure names what diverged.
    for section in expected:
        assert actual.get(section) == expected[section], f"golden section {section!r} diverged"
    assert set(actual) == set(expected)


@pytest.mark.parametrize("scenario_path", _scenario_paths(), ids=lambda p: p.stem)
async def test_scripted_replay_is_deterministic(scenario_path: Path, checkpointer: Checkpointer) -> None:
    """A golden is only an oracle if the recording itself is reproducible."""
    assert await _run(scenario_path, checkpointer) == await _run(scenario_path, checkpointer)
