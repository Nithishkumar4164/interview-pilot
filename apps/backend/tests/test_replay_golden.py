"""Golden parity for the real-time pipeline under the scripted LLM.

These goldens were recorded from the pre-LangGraph AnalysisWorker. Any
reimplementation of the real-time pipeline must reproduce them.

To re-record after an intentional behavior change:
    REPLAY_UPDATE_GOLDEN=1 uv run pytest tests/test_replay_golden.py
and review the diff before committing.
"""

import os
from pathlib import Path

import pytest

from src.replay.golden import golden_path, normalize, read_golden, write_golden
from src.replay.runner import ReplayRunner
from src.replay.scenario import load_scenario

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
GOLDEN_DIR = Path(__file__).parent / "golden"
UPDATE = os.environ.get("REPLAY_UPDATE_GOLDEN") == "1"


def _scenario_paths() -> list[Path]:
    return sorted(SCENARIOS_DIR.glob("*.scenario.json"))


async def _run(path: Path) -> dict:
    scenario = load_scenario(path)
    report = await ReplayRunner(scenario, llm_mode="scripted").run()
    return normalize(report)


@pytest.mark.parametrize("scenario_path", _scenario_paths(), ids=lambda p: p.stem)
@pytest.mark.asyncio
async def test_replay_matches_golden(scenario_path: Path) -> None:
    actual = await _run(scenario_path)
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
@pytest.mark.asyncio
async def test_scripted_replay_is_deterministic(scenario_path: Path) -> None:
    """A golden is only an oracle if the recording itself is reproducible."""
    assert await _run(scenario_path) == await _run(scenario_path)
