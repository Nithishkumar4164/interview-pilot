"""Golden parity for plan generation under the scripted LLM.

The goldens were recorded from the sequential (pre-LangGraph) PlanService.
Any reimplementation must produce the same plans and make the same LLM calls
(compared as a multiset: a parallel implementation may reorder them).

Re-record after an intentional change with:
    PLAN_UPDATE_GOLDEN=1 uv run pytest tests/test_plan_golden.py
"""

import json
import os
from pathlib import Path

import pytest

from src.replay.runner import build_scripted_failover_manager
from src.replay.scripted_provider import LLMCallRecorder, recording
from src.schemas.session import InterviewLevel
from src.services.plan_service import PlanService
from tests.test_plan_service import _sample_bank_questions

GOLDEN = Path(__file__).parent / "golden" / "plans.golden.json"
UPDATE = os.environ.get("PLAN_UPDATE_GOLDEN") == "1"

CASES = {
    "sde2-coding-sysdesign-behavioral-60": dict(
        level=InterviewLevel.SDE2, duration_minutes=60, focus_areas=["coding", "system_design", "behavioral"]
    ),
    "intern-coding-30": dict(level=InterviewLevel.INTERN, duration_minutes=30, focus_areas=["coding"]),
    "senior-dsa-debugging-api-aptitude-90": dict(
        level=InterviewLevel.SENIOR, duration_minutes=90, focus_areas=["dsa", "debugging", "api_design", "aptitude"]
    ),
    "sde1-with-bank-45": dict(
        level=InterviewLevel.SDE1, duration_minutes=45, focus_areas=["coding", "behavioral"], use_bank=True
    ),
    "staff-with-resume-60": dict(
        level=InterviewLevel.STAFF,
        duration_minutes=60,
        focus_areas=["system_design", "coding"],
        resume_data={"tech_stack": ["Go", "Kafka"], "years_experience": 9},
    ),
}


def _normalize(plan_dict: dict) -> dict:
    """Replace random generated question ids with positional ones."""
    ids: dict[str, str] = {}
    for p_i, phase in enumerate(plan_dict["phases"]):
        for q_i, q in enumerate(phase["questions"]):
            ids[q["id"]] = f"p{p_i}q{q_i}"
            q["id"] = ids[q["id"]]
    return plan_dict


async def _run_case(case: dict) -> dict:
    kwargs = dict(case)
    use_bank = kwargs.pop("use_bank", False)
    service = PlanService(llm=build_scripted_failover_manager())
    recorder = LLMCallRecorder()
    with recording(recorder):
        plan = await service.generate_plan(
            session_id="golden-session",
            bank_questions=_sample_bank_questions() if use_bank else None,
            **kwargs,
        )
    calls = sorted(
        (json.dumps({k: v for k, v in c.items() if k != "seq"}, sort_keys=True) for c in recorder.to_list()),
    )
    return {"plan": _normalize(service.plan_to_dict(plan)), "llm_calls": [json.loads(c) for c in calls]}


@pytest.mark.parametrize("name", list(CASES))
async def test_plan_matches_golden(name: str) -> None:
    actual = json.loads(json.dumps(await _run_case(CASES[name]), default=str))
    goldens = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if UPDATE:
        goldens[name] = actual
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(goldens, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"golden re-recorded: {name}")
    assert name in goldens, "missing golden; record with PLAN_UPDATE_GOLDEN=1"
    assert actual["plan"] == goldens[name]["plan"], "plan diverged"
    assert actual["llm_calls"] == goldens[name]["llm_calls"], "LLM calls diverged"


async def test_plan_llm_calls_run_in_parallel_within_the_bound(monkeypatch) -> None:
    """Phases (then hints) are requested concurrently; max_concurrency caps it."""
    import asyncio

    from src.config import settings
    from src.llm.failover import FailoverManager

    in_flight = peak = 0
    original = FailoverManager.analyze

    async def tracked(self, *args, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.05)
            return await original(self, *args, **kwargs)
        finally:
            in_flight -= 1

    monkeypatch.setattr(FailoverManager, "analyze", tracked)
    case = CASES["sde2-coding-sysdesign-behavioral-60"]  # 3 phases, 3 hinted questions

    monkeypatch.setattr(settings, "plan_generation_max_concurrency", 8)
    await _run_case(case)
    assert peak == 3, f"expected the 3 phases to be generated concurrently, peak={peak}"

    peak = 0
    monkeypatch.setattr(settings, "plan_generation_max_concurrency", 2)
    await _run_case(case)
    assert peak == 2
