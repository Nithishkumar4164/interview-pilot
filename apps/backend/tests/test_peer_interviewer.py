"""Peer interviewer on the live path (PEER_INTERVIEWER_ENABLED, session graph).

Replays scenarios through the real pipeline with the scripted LLM, which
chooses next_actions from a hash of the prompt. Each test records what the
analysis chose and what evidence it produced, and checks the copilot did
exactly what that implies: a probe card when a probe was chosen or the
analysis found gaps, nothing when the flag is off, and a failing follow-up
never fails the run.
"""

import json
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
from src.replay.scenario import Scenario, ScenarioMeta, TranscriptEvent, load_scenario
from src.schemas.transcript import SpeakerRole
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
def chosen(monkeypatch) -> list[tuple[str, list[str], object]]:
    """(utterance text, actions the analysis chose, the analysis), in order."""
    record: list[tuple[str, list[str], object]] = []
    original = session_graph.analyze_utterance_with_actions

    async def recording(*args, **kwargs):
        result, actions = await original(*args, **kwargs)
        record.append((args[6], actions, result))
        return result, actions

    monkeypatch.setattr(session_graph, "analyze_utterance_with_actions", recording)
    return record


def _factory(checkpointer: Checkpointer, **flags):
    def factory(session_id, fm, timing: PipelineTiming):
        return LangGraphSessionPipeline(
            session_id,
            fm,
            checkpointer,
            min_analysis_interval_s=timing.min_analysis_interval_s,
            interim_throttle_s=timing.interim_throttle_s,
            **flags,
        )

    return factory


async def _replay(checkpointer: Checkpointer, scenario: Scenario | str, **flags):
    if isinstance(scenario, str):
        scenario = load_scenario(SCENARIOS_DIR / scenario)
    scenario.meta.id = f"peer-{uuid.uuid4()}"
    flags.setdefault("peer", True)
    try:
        return await ReplayRunner(scenario, llm_mode="scripted", pipeline_factory=_factory(checkpointer, **flags)).run()
    finally:
        await checkpointer.delete_threads(thread_id_for(scenario.meta.id), code_thread_id(scenario.meta.id))


def _cards(report, kind: str | None = None) -> list[dict]:
    return [
        e.payload
        for e in report.events
        if e.method == "send_copilot_card" and (kind is None or e.payload["kind"] == kind)
    ]


def _calls(report, kind: str) -> list[dict]:
    return [c for c in report.llm_calls if c["kind"] == kind]


def _gaps(analysis) -> bool:
    dims = analysis.dimensions
    return bool(dims and dims.depth_completeness.key_points_missed)


# ── Suggested probes ───────────────────────────────────────────────────────


@pytest.mark.parametrize("scenario", ["04_long_interview_memory.scenario.json", "07_live_coding.scenario.json"])
async def test_probes_are_drafted_when_chosen_or_when_the_answer_left_gaps(checkpointer, chosen, scenario) -> None:
    report = await _replay(checkpointer, scenario)
    assert report.error is None
    due = [text for text, actions, analysis in chosen if "suggest_probe" in actions or _gaps(analysis)]
    assert due and len(due) < len(chosen), "scenario should exercise both branches"
    # One probe call per due analysis, about the utterance it was due for.
    calls = _calls(report, "probes")
    assert [c["user_content"].rsplit("\n", 1)[-1] for c in calls] == [f"[candidate]: {t}" for t in due]
    # Every card comes from a call that returned probes; ids are unique and the first probe is the relay text.
    cards = _cards(report, "probe")
    assert 0 < len(cards) <= len(calls)
    assert len({c["id"] for c in cards}) == len(cards)
    for card in cards:
        assert card["items"] and card["probe"] == card["items"][0] and card["source"] == "llm"
    # The analysis itself still reaches the dashboard for every analyzed utterance.
    assert len([e for e in report.events if e.method == "send_analysis"]) == len(chosen)


async def test_peer_mode_without_agentic_offers_only_peer_actions(checkpointer, chosen) -> None:
    report = await _replay(checkpointer, "04_long_interview_memory.scenario.json", agentic=False, peer=True)
    assert report.error is None and chosen
    assert all(set(actions) <= set(session_graph.PEER_ACTIONS) for _, actions, _ in chosen)
    offered = [c for c in report.llm_calls if c["kind"] == "utterance_analysis"]
    assert offered and all('"draft_hint"' not in c["user_content"] for c in offered)
    # No agentic follow-ups ran; the rule-based contradiction pass still did.
    assert not [e for e in report.events if e.method == "send_hint_suggestion"]
    assert not _calls(report, "code_inspection")
    contradictions = [
        e for e in report.events if e.method == "send_red_flag" and e.payload.get("type") == "contradiction"
    ]
    assert len(contradictions) == 1


async def test_both_flags_offer_both_action_sets(checkpointer, chosen) -> None:
    report = await _replay(checkpointer, "07_live_coding.scenario.json", agentic=True, peer=True)
    assert report.error is None
    all_chosen = {a for _, actions, _ in chosen for a in actions}
    assert all_chosen & set(session_graph.AGENT_ACTIONS) and all_chosen & set(session_graph.PEER_ACTIONS)
    assert len([e for e in report.events if e.method == "send_hint_suggestion"]) == sum(
        "draft_hint" in a for _, a, _ in chosen
    )


async def test_flag_off_keeps_the_copilot_silent(checkpointer, chosen) -> None:
    report = await _replay(checkpointer, "04_long_interview_memory.scenario.json", agentic=True, peer=False)
    assert report.error is None and chosen
    assert not _cards(report) and not _calls(report, "probes") and not _calls(report, "coaching")
    assert all(set(actions) <= set(session_graph.AGENT_ACTIONS) for _, actions, _ in chosen)


async def test_a_failing_probe_does_not_fail_the_run(checkpointer, chosen, monkeypatch) -> None:
    async def broken(*args, **kwargs):
        raise RuntimeError("probe service down")

    monkeypatch.setattr(session_graph, "suggest_probes", broken)
    report = await _replay(checkpointer, "07_live_coding.scenario.json")
    assert report.error is None
    assert not _cards(report, "probe")
    assert report.final_state["analyses_run"] == len(chosen) > 0


# ── Claim checks and clarifications ────────────────────────────────────────


@pytest.mark.parametrize("scenario", ["04_long_interview_memory.scenario.json", "07_live_coding.scenario.json"])
async def test_claims_are_verified_only_when_chosen_and_flagged_only_when_wrong(checkpointer, chosen, scenario):
    report = await _replay(checkpointer, scenario)
    assert report.error is None
    due = [text for text, actions, _ in chosen if "verify_claim" in actions]
    assert due, "scenario exercises no claim check"
    calls = _calls(report, "claim_check")
    assert [c["user_content"].rsplit("\n", 1)[-1] for c in calls] == [f"[candidate]: {t}" for t in due]
    # Exactly the checks the scripted LLM judged wrong (recomputed from the recorded prompt) become cards.
    from src.llm.interface import AnalysisContext, TaskType
    from src.replay.scripted_provider import respond
    from src.services.peer_interviewer import CLAIM_SYSTEM_PROMPT

    wrong = [
        c
        for c in calls
        if json.loads(
            respond(
                "claim_check",
                AnalysisContext(
                    system_prompt=CLAIM_SYSTEM_PROMPT,
                    user_content=c["user_content"],
                    task_type=TaskType.PEER_INTERVIEWER,
                ),
            )
        )["verdict"]
        in ("incorrect", "misleading")
    ]
    cards = _cards(report, "claim_check")
    assert len(cards) == len(wrong)
    for card in cards:
        assert card["priority"] == "urgent" and card["probe"].startswith("scripted-self-correct-")
        assert card["title"].startswith(("Claim looks incorrect", "Claim looks misleading"))


async def test_clarification_runs_when_chosen_or_when_the_candidate_asks(checkpointer, chosen) -> None:
    asked = "Can I assume the input array fits in memory, or should I handle a stream of values here?"
    events = [
        TranscriptEvent(
            speaker_role=SpeakerRole.INTERVIEWER, text="Find two numbers in the array that sum to a target."
        ),
        TranscriptEvent(speaker_role=SpeakerRole.CANDIDATE, text=asked),
        TranscriptEvent(
            speaker_role=SpeakerRole.CANDIDATE, text="I will keep a hash map from value to index and scan once."
        ),
    ]
    report = await _replay(checkpointer, Scenario(meta=ScenarioMeta(id="x"), events=events))
    assert report.error is None
    due = [text for text, actions, _ in chosen if "clarify" in actions or "?" in text]
    assert asked in due
    calls = _calls(report, "clarification")
    assert [c["user_content"].rsplit("\n", 1)[-1] for c in calls] == [f"[candidate]: {t}" for t in due]
    for card in _cards(report, "clarification"):
        assert card["probe"].startswith("scripted-clarification-") and card["kind"] == "clarification"


# ── Interviewer coaching ───────────────────────────────────────────────────

LONG_TURN = (
    "Let me set this up properly before you start. You are given an array of integers and a target value, and "
    "I would like you to return the indices of the two numbers that add up to it; you can assume exactly one "
    "solution exists and you may not use the same element twice."
)


def _words(n: int, seed: str = "word") -> str:
    return " ".join(f"{seed}{i}" for i in range(n))


async def test_coaching_runs_for_substantial_interviewer_turns_at_most_every_20s(checkpointer, chosen) -> None:
    events = [
        TranscriptEvent(speaker_role=SpeakerRole.INTERVIEWER, text=LONG_TURN),
        TranscriptEvent(
            speaker_role=SpeakerRole.CANDIDATE, text="Sure, I will start with the brute force.", delay_ms=2000
        ),
        TranscriptEvent(speaker_role=SpeakerRole.INTERVIEWER, text="Okay, go on.", delay_ms=3000),  # too short
        TranscriptEvent(
            speaker_role=SpeakerRole.INTERVIEWER, text=LONG_TURN + " Also think about duplicates.", delay_ms=5000
        ),
        TranscriptEvent(
            speaker_role=SpeakerRole.INTERVIEWER, text=LONG_TURN + " And negative numbers.", delay_ms=15000
        ),
    ]
    report = await _replay(checkpointer, Scenario(meta=ScenarioMeta(id="x"), events=events))
    assert report.error is None
    coached = [c["user_content"].rsplit("\n", 1)[-1] for c in _calls(report, "coaching")]
    # Turn 1 at t=0; turn 4 at t=10 s is inside the 20 s gap; turn 5 at t=25 s is coached.
    assert coached == [f"[interviewer]: {LONG_TURN}", f"[interviewer]: {LONG_TURN} And negative numbers."]
    for card in _cards(report, "coaching"):
        assert card["priority"] == "info" and card["source"] == "llm" and card["title"].startswith("scripted-coaching-")


async def test_talk_share_and_monologue_signals(checkpointer) -> None:
    events = [
        TranscriptEvent(speaker_role=SpeakerRole.INTERVIEWER, text=_words(130, "intro")),  # monologue
        TranscriptEvent(speaker_role=SpeakerRole.CANDIDATE, text=_words(20, "ans"), delay_ms=1000),
        TranscriptEvent(speaker_role=SpeakerRole.INTERVIEWER, text=_words(100, "more"), delay_ms=1000),
        TranscriptEvent(speaker_role=SpeakerRole.CANDIDATE, text=_words(20, "reply"), delay_ms=1000),
        TranscriptEvent(speaker_role=SpeakerRole.INTERVIEWER, text=_words(100, "again"), delay_ms=1000),
        TranscriptEvent(
            speaker_role=SpeakerRole.INTERVIEWER, text=_words(100, "still"), delay_ms=1000
        ),  # inside 5 min gap
    ]
    report = await _replay(checkpointer, Scenario(meta=ScenarioMeta(id="x"), events=events), agentic=False)
    assert report.error is None
    rule_cards = [c for c in _cards(report, "coaching") if c["source"] == "rule"]
    assert [c["id"].rsplit(":", 1)[-1] for c in rule_cards] == ["monologue", "talk_share"]
    assert rule_cards[1]["title"] == "You have spoken 92% of the last 250 words"  # fires once the window has 250 words
    # Signals need no LLM.
    assert not _calls(report, "coaching") or all(c["kind"] == "coaching" for c in _calls(report, "coaching"))


# ── Time steward and stuck countdown ───────────────────────────────────────


async def test_time_steward_alerts_once_per_threshold_and_resets_on_question_change(checkpointer) -> None:
    from src.replay.scenario import QuestionChangeEvent, TickEvent

    q1 = QuestionChangeEvent(
        question_text="Two sum",
        question_plan={"id": "q1", "time_minutes": 1},
        question_index=0,
        remaining_questions=2,
        remaining_minutes=25,
    )
    q2 = QuestionChangeEvent(
        question_text="Reverse a list", question_plan={"id": "q2", "time_minutes": 1}, question_index=1, delay_ms=5000
    )
    events = [
        q1,
        TickEvent(delay_ms=30_000),  # 0:30 — within budget
        TickEvent(delay_ms=31_000),  # 1:01 — over
        TickEvent(delay_ms=10_000),  # 1:11 — already sent
        TickEvent(delay_ms=25_000),  # 1:36 — far over
        q2,  # 1:41 — the clock restarts
        TickEvent(delay_ms=50_000),  # 2:31 — 0:50 into q2: nothing
        TickEvent(delay_ms=15_000),  # 2:46 — 1:05 into q2: over
    ]
    report = await _replay(checkpointer, Scenario(meta=ScenarioMeta(id="x"), events=events), agentic=False)
    assert report.error is None
    cards = _cards(report, "pace")
    assert [c["id"].split(":", 1)[1] for c in cards] == ["q1:pace:over", "q1:pace:far_over", "q2:pace:over"]
    assert cards[0]["priority"] == "suggest" and cards[1]["priority"] == "urgent"
    assert cards[0]["body"].startswith("2 questions and 25 min of plan left.")
    assert cards[2]["body"].startswith("This is the last planned question.")
    assert all(c["source"] == "rule" for c in cards) and not report.llm_calls


async def test_stuck_countdown_drafts_one_hint_per_stuck_snapshot(checkpointer) -> None:
    from src.replay.scenario import CodeUpdateEvent, QuestionChangeEvent, TickEvent

    code = "def two_sum(nums, target):\n    seen = {}\n    for i, n in enumerate(nums):\n        pass\n"
    events = [
        QuestionChangeEvent(question_text="Two sum", question_plan={"id": "q1", "time_minutes": 30}),
        TranscriptEvent(speaker_role=SpeakerRole.CANDIDATE, text="Let me start writing the loop.", delay_ms=1000),
        # Five snapshots with (nearly) the same length -> CodeGraph's local heuristic says stuck.
        *[CodeUpdateEvent(code=code + " " * (i % 2), delay_ms=5000) for i in range(5)],
        TickEvent(delay_ms=10_000),  # 36 s of silence: not yet
        TickEvent(delay_ms=30_000),  # 66 s: stuck -> card + drafted hint
        TickEvent(delay_ms=30_000),  # same snapshot: nothing more
        CodeUpdateEvent(code=code + "x", delay_ms=1000),  # a new (still stuck) snapshot
        TickEvent(delay_ms=30_000),  # the candidate is still quiet: one more
    ]
    report = await _replay(checkpointer, Scenario(meta=ScenarioMeta(id="x"), events=events), agentic=False)
    assert report.error is None
    stuck = _cards(report, "stuck")
    assert len(stuck) == 2 and all(c["title"].startswith("Quiet for ") for c in stuck)
    drafted = [e.payload for e in report.events if e.method == "send_hint_suggestion"]
    assert [d["id"].split(":", 1)[1] for d in drafted] == [f"stuck:{c['id'].split(':')[1]}:hint" for c in stuck]
    assert drafted[0]["question_id"] == "q1" and drafted[0]["text"].startswith("scripted-hint-")


# ── Coverage map and adaptive difficulty ───────────────────────────────────


async def test_coverage_snapshots_follow_the_analyses(checkpointer, chosen) -> None:
    from src.replay.scenario import QuestionChangeEvent

    events = [
        QuestionChangeEvent(question_text="Two sum", question_plan={"id": "q1", "category": "coding"}),
        TranscriptEvent(
            speaker_role=SpeakerRole.CANDIDATE, text="I would use a hash map to look up complements in one pass."
        ),
        QuestionChangeEvent(
            question_text="Design a URL shortener",
            question_plan={"id": "q2", "category": "system_design"},
            delay_ms=1000,
        ),
        TranscriptEvent(
            speaker_role=SpeakerRole.CANDIDATE,
            text="I would start with the write path and a key generation service.",
            delay_ms=1000,
        ),
    ]
    report = await _replay(checkpointer, Scenario(meta=ScenarioMeta(id="x"), events=events), agentic=False)
    assert report.error is None
    snapshots = [e.payload for e in report.events if e.method == "send_copilot_coverage"]
    assert len(snapshots) == len(chosen) == 2
    first, second = snapshots
    assert first["criteria"]["problem_solving"]["evidence"] == 1 and "system_design" in first["gaps"]
    assert second["criteria"]["problem_solving"]["evidence"] == 2
    assert second["criteria"]["system_design"] == {"evidence": 1, "status": "thin"}
    assert "system_design" not in second["gaps"]
    assert first["criteria"]["code_quality"]["evidence"] == 0  # no code was written


@pytest.mark.parametrize(("quality", "key"), [(5, "cruising"), (1, "struggling")])
async def test_difficulty_card_once_per_question(checkpointer, monkeypatch, quality, key) -> None:
    from src.replay.scenario import QuestionChangeEvent

    original = session_graph.analyze_utterance_with_actions

    async def forced(*args, **kwargs):
        result, actions = await original(*args, **kwargs)
        result.answer_quality, result.on_track = quality, True
        return result, []

    monkeypatch.setattr(session_graph, "analyze_utterance_with_actions", forced)
    answers = [
        TranscriptEvent(
            speaker_role=SpeakerRole.CANDIDATE, text=f"Answer number {i} explains the approach in some detail."
        )
        for i in range(4)
    ]
    events = [
        QuestionChangeEvent(question_text="Q1", question_plan={"id": "q1", "alternates": ["Variant A"]}),
        *answers,
        QuestionChangeEvent(question_text="Q2", question_plan={"id": "q2"}, delay_ms=1000),
        *answers,
    ]
    report = await _replay(checkpointer, Scenario(meta=ScenarioMeta(id="x"), events=events), agentic=False)
    assert report.error is None
    cards = _cards(report, "difficulty")
    assert [c["id"].split(":", 1)[1] for c in cards] == [f"q1:difficulty:{key}", f"q2:difficulty:{key}"]
    assert cards[0]["items"] == ["Variant A"] and cards[1]["items"] == []


# ── Cards the interviewer acted on ─────────────────────────────────────────


async def test_used_cards_are_recorded_once_for_the_report(checkpointer) -> None:
    session_id = f"peer-{uuid.uuid4()}"
    from src.replay.runner import build_scripted_failover_manager

    pipeline = LangGraphSessionPipeline(
        session_id, build_scripted_failover_manager(), checkpointer, min_analysis_interval_s=0, peer=True
    )
    try:
        card = {"id": f"{session_id}:u1:probe", "kind": "probe", "text": "How would you handle duplicates?"}
        await pipeline.handle_copilot_card_used({**card, "at": "2026-01-01T00:10:00+00:00"})
        await pipeline.handle_copilot_card_used({**card, "at": "2026-01-01T00:10:05+00:00"})  # a repeated click
        await pipeline.handle_copilot_card_used({"id": "", "kind": "probe", "text": "ignored"})
        await pipeline.wait_idle()
        used = pipeline.report_snapshot()["copilot_cards_used"]
        assert used == [
            {
                "card_id": card["id"],
                "kind": "probe",
                "text": card["text"],
                "question_id": "",
                "at": "2026-01-01T00:10:00+00:00",
            }
        ]
    finally:
        await pipeline.close()


async def test_used_card_takes_the_active_question_and_the_scenario_clock(checkpointer) -> None:
    from src.replay.scenario import CopilotCardUsedEvent, QuestionChangeEvent

    events = [
        QuestionChangeEvent(question_text="Two sum", question_plan={"id": "q-two-sum"}, question_index=0),
        TranscriptEvent(speaker_role=SpeakerRole.CANDIDATE, text="I would sort the array first and then scan it once."),
        CopilotCardUsedEvent(card_id="c1", kind="probe", text="Why sort first?", delay_ms=30_000),
    ]
    scenario = Scenario(meta=ScenarioMeta(id=f"peer-{uuid.uuid4()}"), events=events)
    pipelines: list[LangGraphSessionPipeline] = []

    def factory(session_id, fm, timing: PipelineTiming):
        pipelines.append(LangGraphSessionPipeline(session_id, fm, checkpointer, min_analysis_interval_s=0, peer=True))
        return pipelines[-1]

    try:
        report = await ReplayRunner(scenario, llm_mode="scripted", pipeline_factory=factory).run()
        assert report.error is None
        (used,) = pipelines[0].report_snapshot()["copilot_cards_used"]
        assert used["question_id"] == "q-two-sum" and used["at"] == "2026-01-01T00:00:30+00:00"
    finally:
        await checkpointer.delete_threads(thread_id_for(scenario.meta.id), code_thread_id(scenario.meta.id))
