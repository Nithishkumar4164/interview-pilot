"""Golden parity for the real-time pipeline under the scripted LLM.

These goldens were recorded from the pre-LangGraph AnalysisWorker. Every
implementation of the real-time pipeline must reproduce them; each scenario
runs through AnalysisWorker and through the LangGraph session pipeline (on
the real Postgres checkpointer).

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
from src.replay.runner import PipelineFactory, ReplayRunner, analysis_worker_factory
from src.replay.scenario import load_scenario
from src.services.session_state_service import SessionState, SessionStateService
from src.workers.langgraph_session import LangGraphSessionPipeline, thread_id_for

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


def graph_factory(checkpointer: Checkpointer) -> PipelineFactory:
    def factory(session_id, fm, timing):
        return LangGraphSessionPipeline(
            session_id,
            fm,
            checkpointer,
            min_analysis_interval_s=timing.min_analysis_interval_s,
            interim_throttle_s=timing.interim_throttle_s,
        )

    return factory


async def _run(path: Path, factory: PipelineFactory = analysis_worker_factory) -> dict:
    scenario = load_scenario(path)
    report = await ReplayRunner(scenario, llm_mode="scripted", pipeline_factory=factory).run()
    return normalize(report)


async def _run_graph(path: Path, checkpointer: Checkpointer) -> dict:
    thread = thread_id_for(load_scenario(path).meta.id)
    threads = (thread, code_thread_id(load_scenario(path).meta.id))
    await checkpointer.delete_threads(*threads)
    try:
        return await _run(path, graph_factory(checkpointer))
    finally:
        await checkpointer.delete_threads(*threads)


def _refreshed_session_state(golden: dict) -> dict:
    """SessionStateService's snapshot as it would be if refreshed from the final worker state."""
    service = SessionStateService()
    sid = golden["scenario_id"]
    service._states[sid] = SessionState.from_snapshot({**golden["session_state"], "session_id": sid})
    service.update_from_worker(sid, golden["final_state"])
    snapshot = service.get_state(sid).to_snapshot()
    snapshot.pop("captured_at")
    return snapshot


def _assert_matches(actual: dict, expected: dict) -> None:
    # Compare section by section so a failure names what diverged.
    for section in expected:
        assert actual.get(section) == expected[section], f"golden section {section!r} diverged"
    assert set(actual) == set(expected)


@pytest.mark.parametrize("scenario_path", _scenario_paths(), ids=lambda p: p.stem)
async def test_langgraph_pipeline_matches_golden(scenario_path: Path, checkpointer: Checkpointer) -> None:
    path = golden_path(GOLDEN_DIR, load_scenario(scenario_path).meta.id)
    assert path.exists(), f"missing golden {path}; record with REPLAY_UPDATE_GOLDEN=1"
    expected = read_golden(path)
    actual = await _run_graph(scenario_path, checkpointer)
    # The graph serves the session-state snapshot current; SessionStateService
    # refreshed it only when an analysis ran. Compare with what it would hold
    # refreshed at the end.
    assert actual.pop("session_state") == _refreshed_session_state(expected)
    _assert_matches(actual, {k: v for k, v in expected.items() if k != "session_state"})


@pytest.mark.parametrize("scenario_path", _scenario_paths(), ids=lambda p: p.stem)
@pytest.mark.asyncio
async def test_replay_matches_golden(scenario_path: Path) -> None:
    actual = await _run(scenario_path)
    path = golden_path(GOLDEN_DIR, actual["scenario_id"])

    if UPDATE:
        write_golden(path, actual)
        pytest.skip(f"golden re-recorded: {path.name}")

    assert path.exists(), f"missing golden {path}; record with REPLAY_UPDATE_GOLDEN=1"
    _assert_matches(actual, read_golden(path))


@pytest.mark.parametrize("scenario_path", _scenario_paths(), ids=lambda p: p.stem)
@pytest.mark.asyncio
async def test_scripted_replay_is_deterministic(scenario_path: Path) -> None:
    """A golden is only an oracle if the recording itself is reproducible."""
    assert await _run(scenario_path) == await _run(scenario_path)
