"""Agentic routing on the live path (AGENTIC_ROUTING_ENABLED, session graph).

The scripted LLM chooses next_actions from a hash of the prompt, so each test
records what was chosen for every analysis and checks the pipeline did
exactly that: the rule-based contradiction pass always, the LLM pass, a
drafted hint and a code check only when chosen, never more than once, and a
failing or slow follow-up never fails the run or delays the analysis push.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest

from src.config import settings
from src.graphs import session_graph
from src.graphs.checkpointer import Checkpointer
from src.graphs.code_graph import code_thread_id
from src.replay.runner import PipelineTiming, ReplayRunner
from src.replay.scenario import load_scenario
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
def chosen(monkeypatch) -> list[tuple[str, list[str]]]:
    """(utterance text, actions the analysis chose), in order."""
    record: list[tuple[str, list[str]]] = []
    original = session_graph.analyze_utterance_with_actions

    async def recording(*args, **kwargs):
        result, actions = await original(*args, **kwargs)
        record.append((args[-1], actions))
        return result, actions

    monkeypatch.setattr(session_graph, "analyze_utterance_with_actions", recording)
    return record


async def _replay(checkpointer: Checkpointer, scenario_file: str, agentic: bool = True):
    scenario = load_scenario(SCENARIOS_DIR / scenario_file)
    scenario.meta.id = f"agentic-{uuid.uuid4()}"

    def factory(session_id, fm, timing: PipelineTiming):
        return LangGraphSessionPipeline(
            session_id,
            fm,
            checkpointer,
            min_analysis_interval_s=timing.min_analysis_interval_s,
            interim_throttle_s=timing.interim_throttle_s,
            agentic=agentic,
        )

    try:
        return await ReplayRunner(scenario, llm_mode="scripted", pipeline_factory=factory).run()
    finally:
        await checkpointer.delete_threads(thread_id_for(scenario.meta.id), code_thread_id(scenario.meta.id))


def _events(report, method: str, kind: str | None = None) -> list[dict]:
    return [
        e.payload
        for e in report.events
        if e.method == method and (kind is None or (isinstance(e.payload, dict) and e.payload.get("type") == kind))
    ]


@pytest.mark.parametrize("scenario", ["04_long_interview_memory.scenario.json", "07_live_coding.scenario.json"])
async def test_each_follow_up_runs_exactly_when_chosen(checkpointer, chosen, scenario) -> None:
    report = await _replay(checkpointer, scenario)
    assert report.error is None
    analyses = _events(report, "send_analysis")
    assert len(chosen) == len(analyses) > 0
    assert any(actions for _, actions in chosen), "scenario exercises no follow-up"

    # Drafted hints: one per analysis that chose draft_hint.
    drafted = _events(report, "send_hint_suggestion")
    assert len(drafted) == sum("draft_hint" in a for _, a in chosen)
    assert len({d["id"] for d in drafted}) == len(drafted)

    # LLM contradiction checks only for statements whose analysis chose them.
    checked = {text for text, a in chosen if "check_contradiction" in a}
    for call in (c for c in report.llm_calls if c["kind"] == "contradiction"):
        new_statement = call["user_content"].split('New statement by the candidate:\n"', 1)[1].split('"\n', 1)[0]
        assert new_statement in checked, f"LLM contradiction check not chosen for: {new_statement[:60]}"

    # Code checks only when chosen (and only when there is code to check).
    inspections = [c for c in report.llm_calls if c["kind"] == "code_inspection"]
    assert len(inspections) <= sum("inspect_code" in a for _, a in chosen)
    for call in inspections:
        statement = call["user_content"].split('What the candidate said:\n"', 1)[1].split('"\n', 1)[0]
        assert any(text == statement and "inspect_code" in a for text, a in chosen)

    # The hint signal is still the last thing each analysis produces.
    assert len(_events(report, "send_hint")) == sum(1 for a in analyses if a["hint_signal"] != "none")


async def test_rule_based_contradictions_are_flagged_even_when_not_chosen(checkpointer, chosen) -> None:
    """Scenario 04's real contradiction is caught by the rule pass, which always runs."""
    report = await _replay(checkpointer, "04_long_interview_memory.scenario.json")
    flags = _events(report, "send_red_flag", "contradiction")
    denial = next(text for text, _ in chosen if text.startswith("Actually I've never used Kafka"))
    assert [f["new_statement"] for f in flags if f["new_statement"] == denial]
    assert all(f["claim"] not in f["new_statement"] for f in flags)


async def test_code_check_flags_a_mismatch(checkpointer, chosen) -> None:
    report = await _replay(checkpointer, "07_live_coding.scenario.json")
    inspections = [c for c in report.llm_calls if c["kind"] == "code_inspection"]
    mismatches = _events(report, "send_red_flag", "code_mismatch")
    assert inspections, "scenario 07 never ran a code check"
    for flag in mismatches:
        assert flag["explanation"].startswith("scripted-code-mismatch-") and flag["suggested_probe"]


async def test_a_failing_follow_up_does_not_fail_the_run(checkpointer, chosen, monkeypatch) -> None:
    async def broken(*args, **kwargs):
        raise RuntimeError("inspection service down")

    async def broken_hint(*args, **kwargs):
        raise ConnectionError("provider unreachable")  # transient: retried once, then given up

    monkeypatch.setattr(session_graph, "inspect_code_against_statement", broken)
    monkeypatch.setattr(session_graph, "generate_runtime_hint", broken_hint)
    report = await _replay(checkpointer, "07_live_coding.scenario.json")
    assert report.error is None
    assert len(_events(report, "send_analysis")) == len(chosen)
    assert not _events(report, "send_hint_suggestion") and not _events(report, "send_red_flag", "code_mismatch")
    assert report.final_state["analyses_run"] == len(chosen)
    # Every run finished: the hint signals come from the node after the follow-ups.
    analyses = _events(report, "send_analysis")
    assert len(_events(report, "send_hint")) == sum(1 for x in analyses if x["hint_signal"] != "none") > 0


async def test_a_slow_follow_up_neither_delays_the_analysis_nor_hangs(checkpointer, chosen, monkeypatch) -> None:
    from datetime import datetime

    monkeypatch.setattr(session_graph, "ACTION_TIMEOUT_S", 0.3)

    async def slow(*args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(session_graph, "generate_runtime_hint", slow)
    monkeypatch.setattr(session_graph, "inspect_code_against_statement", slow)
    monkeypatch.setattr(session_graph, "check_contradictions", slow)

    started = time.perf_counter()
    report = await _replay(checkpointer, "07_live_coding.scenario.json")
    elapsed = time.perf_counter() - started
    assert report.error is None and len(_events(report, "send_analysis")) == len(chosen)
    # Each analysis reaches the dashboard right after its transcript, before the follow-ups.
    last_transcript = None
    for e in report.events:
        at = datetime.fromisoformat(e.timestamp)
        if e.method == "send_transcript":
            last_transcript = at
        elif e.method == "send_analysis":
            assert last_transcript is not None
            assert (at - last_transcript).total_seconds() < 0.25
    # Every run's follow-ups were cut off at 0.3 s instead of running 5 s.
    assert elapsed < 5


async def test_flag_off_keeps_the_fixed_follow_up(checkpointer, chosen) -> None:
    report = await _replay(checkpointer, "04_long_interview_memory.scenario.json", agentic=False)
    assert chosen == []  # the agentic analysis was never used
    assert not _events(report, "send_hint_suggestion")
    assert len(_events(report, "send_red_flag", "contradiction")) == 1


@pytest.mark.parametrize("choose_check", [False, True])
async def test_llm_contradiction_check_runs_only_when_chosen(checkpointer, monkeypatch, choose_check) -> None:
    """Same statements both ways: a Kafka claim, then another Kafka statement no rule
    decides. The LLM confirmation runs only if the analysis chose check_contradiction."""
    from src.replay.scenario import Scenario, ScenarioMeta, TranscriptEvent
    from src.schemas.transcript import SpeakerRole

    original = session_graph.analyze_utterance_with_actions

    async def forced(*args, **kwargs):
        result, _ = await original(*args, **kwargs)
        return result, ["check_contradiction"] if choose_check else []

    monkeypatch.setattr(session_graph, "analyze_utterance_with_actions", forced)
    claim = (
        "I have five years of experience running Kafka clusters in production, mostly building the "
        "ingestion pipelines and the consumer services for our analytics platform."
    )
    events = [
        TranscriptEvent(speaker_role=SpeakerRole.CANDIDATE, text=claim, channel="microphone"),
        TranscriptEvent(
            speaker_role=SpeakerRole.CANDIDATE,
            text="We tuned Kafka retention down to seven days for the audit topic last year.",
            channel="microphone",
        ),
    ]
    scenario = Scenario(meta=ScenarioMeta(id=f"agentic-{uuid.uuid4()}"), events=events)

    def factory(session_id, fm, timing: PipelineTiming):
        return LangGraphSessionPipeline(session_id, fm, checkpointer, min_analysis_interval_s=0, agentic=True)

    try:
        report = await ReplayRunner(scenario, llm_mode="scripted", pipeline_factory=factory).run()
    finally:
        await checkpointer.delete_threads(thread_id_for(scenario.meta.id), code_thread_id(scenario.meta.id))
    llm_checks = [c for c in report.llm_calls if c["kind"] == "contradiction"]
    assert len(llm_checks) == (1 if choose_check else 0)
