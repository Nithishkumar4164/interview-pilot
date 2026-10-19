"""Copilot follow-ups on the live path (PEER_INTERVIEWER_ENABLED, session graph).

The scripted LLM chooses next_actions from a hash of the prompt, so each test
records what was chosen for every analysis and checks the pipeline did
exactly that: the rule-based contradiction pass always, the claim check's LLM
call, a drafted hint and a code check only when chosen, every result as a
copilot card, and a failing or slow follow-up never fails the run or delays
the analysis push. With the copilot off only the rule pass runs.
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
from src.services.contradiction_service import Contradiction
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
        record.append((args[6], actions))
        return result, actions

    monkeypatch.setattr(session_graph, "analyze_utterance_with_actions", recording)
    return record


def _factory(checkpointer: Checkpointer, peer: bool):
    def factory(session_id, fm, timing: PipelineTiming):
        return LangGraphSessionPipeline(
            session_id,
            fm,
            checkpointer,
            min_analysis_interval_s=timing.min_analysis_interval_s,
            interim_throttle_s=timing.interim_throttle_s,
            peer=peer,
        )

    return factory


async def _replay(checkpointer: Checkpointer, scenario, peer: bool = True):
    if isinstance(scenario, str):
        scenario = load_scenario(SCENARIOS_DIR / scenario)
    scenario.meta.id = f"copilot-{uuid.uuid4()}"
    try:
        return await ReplayRunner(scenario, llm_mode="scripted", pipeline_factory=_factory(checkpointer, peer)).run()
    finally:
        await checkpointer.delete_threads(thread_id_for(scenario.meta.id), code_thread_id(scenario.meta.id))


def _events(report, method: str) -> list[dict]:
    return [e.payload for e in report.events if e.method == method]


def _cards(report, kind: str) -> list[dict]:
    return [c for c in _events(report, "send_copilot_card") if c["kind"] == kind]


def _contradiction_cards(report) -> list[dict]:
    return [c for c in _cards(report, "claim_check") if ":contradiction:" in c["id"]]


@pytest.mark.parametrize("scenario", ["04_long_interview_memory.scenario.json", "07_live_coding.scenario.json"])
async def test_each_follow_up_runs_exactly_when_chosen(checkpointer, chosen, scenario) -> None:
    report = await _replay(checkpointer, scenario)
    assert report.error is None
    analyses = _events(report, "send_analysis")
    assert len(chosen) == len(analyses) > 0
    assert any(actions for _, actions in chosen), "scenario exercises no follow-up"

    # Drafted hints: one hint card per analysis that chose draft_hint (the stuck countdown has its own ids).
    drafted = [c for c in _cards(report, "hint") if ":stuck:" not in c["id"]]
    assert len(drafted) == sum("draft_hint" in a for _, a in chosen)
    assert len({d["id"] for d in drafted}) == len(drafted)
    assert all(d["probe"].startswith("scripted-hint-") for d in drafted)

    # The claim check's LLM call only for statements whose analysis chose verify_claim.
    checked = {text for text, a in chosen if "verify_claim" in a}
    calls = [c for c in report.llm_calls if c["kind"] == "claim_check"]
    assert {c["user_content"].rsplit("[candidate]: ", 1)[1] for c in calls} == checked

    # Code checks only when chosen (and only when there is code to check).
    inspections = [c for c in report.llm_calls if c["kind"] == "code_inspection"]
    assert len(inspections) <= sum("inspect_code" in a for _, a in chosen)
    for call in inspections:
        statement = call["user_content"].split('What the candidate said:\n"', 1)[1].split('"\n', 1)[0]
        assert any(text == statement and "inspect_code" in a for text, a in chosen)

    # Contradictions and code mismatches no longer go out as red flags.
    assert not [f for f in _events(report, "send_red_flag") if "type" in f]
    # The hint signal is still the last thing each analysis produces.
    assert len(_events(report, "send_hint")) == sum(1 for a in analyses if a["hint_signal"] != "none")


async def test_rule_based_contradictions_are_flagged_even_when_not_chosen(checkpointer, chosen) -> None:
    """Scenario 04's real contradiction is caught by the rule pass, which always runs."""
    report = await _replay(checkpointer, "04_long_interview_memory.scenario.json")
    rule = [c for c in _contradiction_cards(report) if c["source"] == "rule"]
    denial = next(text for text, _ in chosen if text.startswith("Actually I've never used Kafka"))
    assert [c for c in rule if c["items"][1] == f"Now: {denial}"]
    assert all(c["priority"] == "urgent" and c["items"][0].startswith("Earlier: ") for c in rule)


async def test_code_check_cards(checkpointer, chosen) -> None:
    import json

    from src.llm.interface import AnalysisContext, TaskType
    from src.replay.scripted_provider import respond
    from src.services.code_analysis_service import CODE_INSPECTION_SYSTEM_PROMPT

    report = await _replay(checkpointer, "07_live_coding.scenario.json")
    inspections = [c for c in report.llm_calls if c["kind"] == "code_inspection"]
    assert inspections, "scenario 07 never ran a code check"
    # Exactly the checks the scripted LLM judged a mismatch (recomputed from the recorded prompt) become cards.
    mismatches = [
        c
        for c in inspections
        if json.loads(
            respond(
                "code_inspection",
                AnalysisContext(
                    system_prompt=CODE_INSPECTION_SYSTEM_PROMPT,
                    user_content=c["user_content"],
                    task_type=TaskType.CODE_ANALYSIS,
                ),
            )
        )["mismatch"]
    ]
    cards = _cards(report, "code_check")
    assert len(cards) == len(mismatches)
    for card in cards:
        assert card["body"].startswith("scripted-code-mismatch-") and card["probe"].startswith("scripted-code-probe-")
        assert card["items"][0].startswith("They said: ") and card["id"].endswith(":code_check")


async def test_a_failing_follow_up_does_not_fail_the_run(checkpointer, chosen, monkeypatch) -> None:
    async def broken(*args, **kwargs):
        raise RuntimeError("inspection service down")

    async def broken_hint(*args, **kwargs):
        raise ConnectionError("provider unreachable")  # transient: retried once, then given up

    monkeypatch.setattr(session_graph, "inspect_code_against_statement", broken)
    monkeypatch.setattr(session_graph, "generate_runtime_hint", broken_hint)
    monkeypatch.setattr(session_graph, "verify_claim", broken)
    report = await _replay(checkpointer, "07_live_coding.scenario.json")
    assert report.error is None
    assert len(_events(report, "send_analysis")) == len(chosen)
    assert not _cards(report, "hint") and not _cards(report, "code_check")
    assert not [c for c in _cards(report, "claim_check") if c["source"] == "llm"]
    assert report.final_state["analyses_run"] == len(chosen)
    # Every run finished: the hint signals come from the node after the follow-ups.
    analyses = _events(report, "send_analysis")
    assert len(_events(report, "send_hint")) == sum(1 for x in analyses if x["hint_signal"] != "none") > 0


async def test_a_slow_follow_up_neither_delays_the_analysis_nor_hangs(checkpointer, chosen, monkeypatch) -> None:
    from datetime import datetime

    monkeypatch.setattr(session_graph, "ACTION_TIMEOUT_S", 0.3)

    async def slow(*args, **kwargs):
        await asyncio.sleep(5)

    for name in ("generate_runtime_hint", "inspect_code_against_statement", "verify_claim", "suggest_probes"):
        monkeypatch.setattr(session_graph, name, slow)

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


async def test_copilot_off_runs_only_the_rule_pass(checkpointer, chosen) -> None:
    report = await _replay(checkpointer, "04_long_interview_memory.scenario.json", peer=False)
    assert report.error is None
    assert chosen == []  # the analysis was never asked to choose follow-ups
    assert not [c for c in report.llm_calls if c["kind"] in ("claim_check", "code_inspection", "probes")]
    cards = _events(report, "send_copilot_card")
    assert len(cards) == 1 and cards[0]["source"] == "rule" and ":contradiction:" in cards[0]["id"]
    assert report.summary.contradictions == 1


@pytest.mark.parametrize("choose_check", [False, True])
async def test_conflicts_the_claim_check_finds_are_recorded_only_when_chosen(
    checkpointer, monkeypatch, choose_check
) -> None:
    """Same statements both ways: a Kafka claim, then a Kafka statement no rule decides.
    The claim check's LLM call runs only if the analysis chose verify_claim, and the
    conflict it reports becomes a card and a recorded contradiction (for the report)."""
    from src.replay.scenario import Scenario, ScenarioMeta, TranscriptEvent
    from src.schemas.transcript import SpeakerRole

    original = session_graph.analyze_utterance_with_actions
    verified: list[tuple[str, list[str]]] = []

    async def forced(*args, **kwargs):
        result, _ = await original(*args, **kwargs)
        return result, ["verify_claim"] if choose_check else []

    async def stub(failover, session_id, context_text, question_plan, speaker, text, earlier):
        verified.append((text, earlier))
        conflicts = [
            Contradiction(
                claim=earlier[0],
                new_statement=text,
                explanation="Five years of Kafka, yet unsure what retention is.",
                confidence=0.9,
                suggested_probe="How did you size retention on those clusters?",
                detection_method="llm",
            )
        ]
        return {"technical": None, "conflicts": conflicts if earlier else []}

    monkeypatch.setattr(session_graph, "analyze_utterance_with_actions", forced)
    monkeypatch.setattr(session_graph, "verify_claim", stub)
    claim = (
        "I have five years of experience running Kafka clusters in production, mostly building the "
        "ingestion pipelines and the consumer services for our analytics platform."
    )
    second = "I'm honestly not sure what Kafka retention even controls, we never touched those settings."
    events = [
        TranscriptEvent(speaker_role=SpeakerRole.CANDIDATE, text=claim, channel="microphone"),
        TranscriptEvent(speaker_role=SpeakerRole.CANDIDATE, text=second, channel="microphone"),
    ]
    scenario = Scenario(meta=ScenarioMeta(id=f"copilot-{uuid.uuid4()}"), events=events)
    pipelines: list[LangGraphSessionPipeline] = []

    def factory(session_id, fm, timing: PipelineTiming):
        pipelines.append(LangGraphSessionPipeline(session_id, fm, checkpointer, min_analysis_interval_s=0, peer=True))
        return pipelines[-1]

    try:
        report = await ReplayRunner(scenario, llm_mode="scripted", pipeline_factory=factory).run()
        assert report.error is None
        recorded = pipelines[0].to_dict()["contradictions"]["contradictions"]
    finally:
        await checkpointer.delete_threads(thread_id_for(scenario.meta.id), code_thread_id(scenario.meta.id))
    llm_cards = [c for c in _contradiction_cards(report) if c["source"] == "llm"]
    if not choose_check:
        assert verified == [] and llm_cards == [] and recorded == []
        return
    # The second statement was checked against the first one's facts (never against its own).
    assert [text for text, _ in verified] == [claim, second]
    assert verified[0][1] == [] and claim in verified[1][1]
    (card,) = llm_cards
    assert card["items"] == [f"Earlier: {claim}", f"Now: {second}"]
    assert card["probe"] == "How did you size retention on those clusters?"
    assert [(c["claim"], c["new_statement"], c["detection_method"]) for c in recorded] == [(claim, second, "llm")]
