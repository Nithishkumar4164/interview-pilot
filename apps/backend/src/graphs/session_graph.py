"""SessionGraph — the real-time interview pipeline for one session, as a graph.

Each run processes one batch of ingress events on thread ``session_id``;
``LangGraphSessionPipeline`` (src/workers/langgraph_session.py) queues events,
chooses the batch and the utterance to analyze, and runs the graph.

    apply_events ─┬─► Send(extract_facts) × long final candidate utterances ─┐
                  ├─► Send(analyze)   (when the ingress chose one) ─┴─► merge_results ─► follow_up ─► END
                  └─► END             (nothing to extract or analyze)                 └─► END (no analysis)

- apply_events:  applies every event in order — memory tiers, question
  context, hints, summaries from the memory task. Emits ``compress`` when the
  unsummarized buffer reaches the trigger size (or on a question change).
- extract_facts: tier-3 fact extraction for one utterance, in parallel with
  analysis, joined before the contradiction check (as against a real provider
  today, where extraction finishes first).
- analyze:       the 6-dimension utterance analysis. Streams the dashboard push
  and the DB write as soon as it returns, so follow-ups do not delay them.
- merge_results: merges extracted facts in utterance order; records the
  analysis in the bounded history windows.
- follow_up:     contradiction check against the fact store, then the hint signal.

Nodes never call the dashboard or the DB. They emit effects with
``get_stream_writer`` and the pipeline's sink performs them, keyed so a
resumed run cannot write a row twice (see EFFECT_* below).

State is bounded: working memory (10), recent analyses/insights (5), the
report digest (50). The transcript and the full analysis history live in
the DB.
"""

import asyncio
import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.llm.failover import FailoverManager
from src.llm.retry import retry_transient
from src.schemas.analysis import EnhancedAnalysisResult
from src.schemas.hint import RuntimeHintRequest
from src.services.analysis_service import (
    AGENT_ACTIONS,
    PEER_ACTIONS,
    analyze_utterance,
    analyze_utterance_with_actions,
)
from src.services.code_analysis_service import inspect_code_against_statement
from src.services.contradiction_service import Contradiction, check_contradictions
from src.services.memory_service import (
    CLAIM_PROVENANCE_SIZE,
    SUMMARY_TRIGGER_COUNT,
    WORKING_MEMORY_SIZE,
    FactStore,
    Utterance,
    assemble_context,
    extract_facts,
    format_utterances,
    should_extract_facts,
)
from src.services.peer_interviewer import (
    COACHING_MIN_GAP_S,
    COACHING_MIN_WORDS,
    MONOLOGUE_WORDS,
    STUCK_QUIET_S,
    TALK_CARD_MIN_GAP_S,
    TALK_SHARE_MIN_WORDS,
    TALK_SHARE_THRESHOLD,
    TALK_WINDOW_SIZE,
    coach_interviewer,
    copilot_card,
    draft_clarification,
    looks_like_a_question,
    monologue_card,
    pace_alert,
    pace_card,
    seconds_between,
    stuck_card,
    suggest_probes,
    talk_share,
    talk_share_card,
    verify_claim,
    word_count,
)
from src.services.runtime_hint_service import assemble_hint_context, generate_runtime_hint, resolve_directness
from src.utils.logging import get_logger

logger = get_logger("graphs.session")

RECENT_ANALYSES = 5  # analyses kept for to_dict and as runtime-hint insights
REPORT_DIGEST_SIZE = 50  # report_snapshot's analysis_results window
COPILOT_USAGE_SIZE = 50  # copilot cards the interviewer acted on, kept for the report

# ── Ingress events ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UtteranceIn:
    """A transcript event (final or interim) entering memory."""

    id: str
    speaker: str
    text: str
    timestamp: str
    is_final: bool = True


@dataclass(frozen=True)
class QuestionChanged:
    question_text: str
    question_plan: dict = field(default_factory=dict)
    question_index: int = 0
    at: str = ""  # when the interviewer switched (the time steward's question clock)
    remaining_questions: int = 0  # planned questions after this one
    remaining_minutes: float = 0.0  # their time budget


@dataclass(frozen=True)
class HintRevealed:
    """The interviewer showed a hint; analyses score hint_reception until the next question."""

    text: str


@dataclass(frozen=True)
class HintAcknowledged:
    hint_id: str
    question_id: str
    quality: str


@dataclass(frozen=True)
class RuntimeHintGenerated:
    question_id: str
    generation_number: int
    text: str


@dataclass(frozen=True)
class SummaryCompressed:
    """Result of a memory compression: the new summary covers the first ``count`` buffered utterances."""

    summary: str
    count: int


@dataclass(frozen=True)
class CopilotCardUsed:
    """The interviewer acted on a copilot card (asked the probe, relayed the clarification, ...).

    Kept so the report can tell copilot-prompted moves from the interviewer's own.
    """

    card_id: str
    kind: str
    text: str
    question_id: str = ""
    at: str = ""


@dataclass(frozen=True)
class Tick:
    """The clock (peer interviewer): pacing and stuck checks run against ``at`` even when nobody speaks."""

    at: str


IngressEvent = (
    UtteranceIn
    | QuestionChanged
    | HintRevealed
    | HintAcknowledged
    | RuntimeHintGenerated
    | SummaryCompressed
    | CopilotCardUsed
    | Tick
)

# ── Effects (custom stream payloads) ──────────────────────────────────────

EFFECT_DASHBOARD = "dashboard"  # {"method", "payload"} -> dashboard_manager.<method>(session_id, payload)
EFFECT_PERSIST = "persist"  # {"event_type", "fields", "idempotency_key"} -> safe_enqueue
EFFECT_COMPRESS = "compress"  # {"upto": n}: compress unsummarized[:n] now
EFFECT_COMPRESS_AFTER_IN_FLIGHT = "compress_after_in_flight"  # question change while a compression runs


def _reset_or_merge(left: dict | None, right: dict | None) -> dict:
    """Per-run scratch channel: ``None`` clears it, a dict merges into it."""
    if right is None:
        return {}
    return {**(left or {}), **right}


class SessionState(TypedDict, total=False):
    session_id: str

    # ── run input (replaced every run) ──
    events: list[IngressEvent]
    analyze: UtteranceIn | None  # the utterance the ingress chose to analyze in this run
    transport_events: int  # transcript events the transport saw since the previous run
    memory_in_flight: int  # utterances a running compression covers (process-local)
    code_context: dict | None  # latest code from CodeGraph (agentic inspect_code)

    # ── memory ──
    working_memory: list[Utterance]
    unsummarized: list[Utterance]
    rolling_summary: str
    fact_store: FactStore
    total_utterances: int

    # ── question context ──
    current_question: str
    question_plan: dict
    question_index: int
    active_question_id: str | None
    active_hint: str

    # ── analysis ──
    recent_analyses: list[EnhancedAnalysisResult]
    recent_insights: list[EnhancedAnalysisResult]  # cleared on question change
    analysis_digest: list[dict]
    analysis_count: int
    quality_sum: int  # over analyses that count toward the average (scored and on track)
    quality_n: int
    problem_solving_score: float | None  # mean quality of the scored analyses among the last 5, when any
    events_processed: int

    # ── contradictions ──
    contradictions: list[Contradiction]
    checked_pairs: set[str]
    claims_by_utterance: dict[str, list[str]]  # facts each recent utterance added (bounded)

    # ── runtime hints ──
    hint_generation_count: dict[str, int]
    last_runtime_hint: str | None

    # ── peer interviewer ──
    copilot_cards_used: list[dict]  # {"card_id", "kind", "text", "question_id", "at"}, bounded
    talk_window: list[list]  # [speaker, words] of the last TALK_WINDOW_SIZE final utterances
    last_coaching_at: str  # timestamp of the interviewer turn last sent for coaching
    last_talk_card_at: str  # timestamp of the last talk-share nudge
    question_started_at: str  # the time steward's clock for the current question
    remaining_questions: int
    remaining_minutes: float
    pace_alerts_sent: list[str]  # pace alert keys already sent for the current question
    last_candidate_at: str  # timestamp of the candidate's last final utterance
    stuck_hint_for: str  # code snapshot id the stuck countdown already drafted a hint for
    stuck_hint_due: bool  # per-run: this run's Tick found the candidate stuck

    # ── per-run scratch ──
    extracted: Annotated[dict[int, dict], _reset_or_merge]
    analysis: EnhancedAnalysisResult | None
    planned_actions: list[str]  # follow-ups the agentic analysis chose this run
    found_contradictions: list[Contradiction] | None  # agentic check -> finish
    checked_now: set[str] | None


def initial_state(session_id: str) -> dict[str, Any]:
    """Values of a session that has not run yet (what the pipeline reads before its first run)."""
    return {
        "session_id": session_id,
        "working_memory": [],
        "unsummarized": [],
        "rolling_summary": "",
        "fact_store": FactStore(),
        "total_utterances": 0,
        "current_question": "",
        "question_plan": {},
        "question_index": 0,
        "active_question_id": None,
        "active_hint": "",
        "recent_analyses": [],
        "recent_insights": [],
        "analysis_digest": [],
        "analysis_count": 0,
        "quality_sum": 0,
        "quality_n": 0,
        "problem_solving_score": None,
        "events_processed": 0,
        "contradictions": [],
        "checked_pairs": set(),
        "claims_by_utterance": {},
        "hint_generation_count": {},
        "last_runtime_hint": None,
        "copilot_cards_used": [],
        "talk_window": [],
        "last_coaching_at": "",
        "last_talk_card_at": "",
        "question_started_at": "",
        "remaining_questions": 0,
        "remaining_minutes": 0.0,
        "pace_alerts_sent": [],
        "last_candidate_at": "",
        "stuck_hint_for": "",
        "stuck_hint_due": False,
        "analysis": None,
    }


def is_analysis_candidate(event: IngressEvent) -> bool:
    """Final candidate utterances of more than five words are analyzed (rate limit permitting)."""
    return (
        isinstance(event, UtteranceIn)
        and event.is_final
        and event.speaker == "candidate"
        and len(event.text.split()) > 5
    )


def _problem_solving_score(recent: list[EnhancedAnalysisResult], previous: float | None) -> float | None:
    scored = [a.answer_quality for a in recent if a.answer_quality > 0]
    return round(sum(scored) / len(scored), 1) if scored else previous


def analysis_digest_row(a: EnhancedAnalysisResult) -> dict:
    return {
        "answer_quality": a.answer_quality,
        "on_track": a.on_track,
        "insight": a.insight,
        "red_flags": a.red_flags,
        "suggestion": a.suggestion_for_interviewer,
        "hint_signal": a.hint_signal,
    }


def quality_counts(a: EnhancedAnalysisResult) -> bool:
    return a.answer_quality > 0 and a.on_track


class ContradictionTask(TypedDict):
    statement: str
    fact_store: FactStore
    checked_pairs: set[str]
    exclude_claims: list[str]
    use_llm: bool


class HintTask(TypedDict):
    utterance_id: str
    question_id: str
    question_plan: dict
    working_memory_text: str
    rolling_summary: str
    recent_insights: list[EnhancedAnalysisResult]


class InspectTask(TypedDict):
    question: str
    statement: str
    code: str
    language: str


class ProbeTask(TypedDict):
    utterance_id: str
    question_id: str
    question_plan: dict
    context_text: str
    analysis: EnhancedAnalysisResult | None
    speaker: str
    text: str


class PeerUtteranceTask(TypedDict):
    """A peer action about one utterance (claim check, clarification)."""

    utterance_id: str
    question_id: str
    question_plan: dict
    context_text: str
    speaker: str
    text: str


class CoachTask(TypedDict):
    utterance_id: str
    timestamp: str
    question: str
    context_text: str
    text: str


ACTION_TIMEOUT_S = 20.0


class ExtractTask(TypedDict):
    index: int
    speaker: str
    text: str


class AnalyzeTask(TypedDict):
    target: UtteranceIn
    context_text: str
    question_plan: dict
    active_hint: str


def build_session_graph(
    failover: FailoverManager, session_id: str, agentic: bool = False, peer: bool = False
) -> StateGraph:
    """The graph for one session (``session_id`` scopes LLM calls and effect keys).

    ``agentic`` (AGENTIC_ROUTING_ENABLED): the analysis also chooses follow-ups,
    run in parallel after it (see ``plan_actions``). Off, the follow-up is the
    fixed contradiction check of today.

    ``peer`` (PEER_INTERVIEWER_ENABLED): the copilot acts as a peer interviewer.
    The analysis may also choose the peer actions (suggested probes, ...),
    run in the same fan-out, and their results reach the dashboard as
    copilot cards. Peer mode uses the fan-out shape even when ``agentic`` is
    off; the agentic actions are offered only when ``agentic`` is on.
    """
    follow_ups = agentic or peer
    offered: tuple[str, ...] = (*(AGENT_ACTIONS if agentic else ()), *(PEER_ACTIONS if peer else ()))

    def emit(kind: str, **data: Any) -> None:
        get_stream_writer()({"effect": kind, **data})

    def emit_card(card: dict) -> None:
        emit(EFFECT_DASHBOARD, method="send_copilot_card", payload=card)

    def apply_events(state: SessionState) -> dict:
        working = list(state.get("working_memory", []))
        unsummarized = list(state.get("unsummarized", []))
        summary = state.get("rolling_summary", "")
        facts = state.get("fact_store") or FactStore()
        total = state.get("total_utterances", 0)
        question: dict[str, Any] = {
            "current_question": state.get("current_question", ""),
            "question_plan": state.get("question_plan", {}),
            "question_index": state.get("question_index", 0),
            "active_question_id": state.get("active_question_id"),
        }
        active_hint = state.get("active_hint", "")
        recent_insights = list(state.get("recent_insights", []))
        hint_counts = dict(state.get("hint_generation_count", {}))
        last_hint = state.get("last_runtime_hint")
        in_flight = state.get("memory_in_flight", 0)
        cards_used = list(state.get("copilot_cards_used", []))
        talk_window = list(state.get("talk_window", []))
        last_talk_card_at = state.get("last_talk_card_at", "")
        pace: dict[str, Any] = {
            "question_started_at": state.get("question_started_at", ""),
            "remaining_questions": state.get("remaining_questions", 0),
            "remaining_minutes": state.get("remaining_minutes", 0.0),
            "pace_alerts_sent": list(state.get("pace_alerts_sent", [])),
        }
        last_candidate_at = state.get("last_candidate_at", "")
        stuck_hint_for = state.get("stuck_hint_for", "")
        stuck_hint_due = False
        code_context = state.get("code_context") or {}

        for ev in state["events"]:
            if isinstance(ev, UtteranceIn):
                if not ev.text.strip():
                    continue
                u = Utterance(speaker=ev.speaker, text=ev.text, timestamp=ev.timestamp, is_final=ev.is_final)
                working = [*working, u][-WORKING_MEMORY_SIZE:]
                unsummarized.append(u)
                total += 1
                if in_flight == 0 and len(unsummarized) >= SUMMARY_TRIGGER_COUNT:
                    emit(EFFECT_COMPRESS, upto=len(unsummarized))
                    in_flight = len(unsummarized)
                if peer and ev.is_final:
                    words = word_count(ev.text)
                    talk_window = [*talk_window, [ev.speaker, words]][-TALK_WINDOW_SIZE:]
                    if ev.speaker == "candidate":
                        last_candidate_at = ev.timestamp
                    if not pace["question_started_at"]:
                        pace["question_started_at"] = ev.timestamp  # no question change yet: the clock starts now
                    if ev.speaker == "interviewer":
                        if words >= MONOLOGUE_WORDS:
                            emit_card(monologue_card(f"{session_id}:{ev.id}:monologue", words, ev.timestamp))
                        share, spoken = talk_share(talk_window)
                        if (
                            spoken >= TALK_SHARE_MIN_WORDS
                            and share >= TALK_SHARE_THRESHOLD
                            and seconds_between(last_talk_card_at, ev.timestamp) >= TALK_CARD_MIN_GAP_S
                        ):
                            emit_card(talk_share_card(f"{session_id}:{ev.id}:talk_share", share, spoken, ev.timestamp))
                            last_talk_card_at = ev.timestamp
            elif isinstance(ev, QuestionChanged):
                # Tier 1 is per-question; tiers 2 and 3 carry over. The leftover
                # buffer is compressed now, or once a running compression lands.
                working = []
                if in_flight:
                    emit(EFFECT_COMPRESS_AFTER_IN_FLIGHT)
                elif unsummarized:
                    emit(EFFECT_COMPRESS, upto=len(unsummarized))
                    in_flight = len(unsummarized)
                question = {
                    "current_question": ev.question_text,
                    "question_plan": ev.question_plan,
                    "question_index": ev.question_index,
                    "active_question_id": ev.question_plan.get("id"),
                }
                active_hint = ""
                recent_insights = []
                pace = {
                    "question_started_at": ev.at,
                    "remaining_questions": ev.remaining_questions,
                    "remaining_minutes": ev.remaining_minutes,
                    "pace_alerts_sent": [],
                }
                stuck_hint_for = ""
            elif isinstance(ev, HintRevealed):
                active_hint = ev.text
            elif isinstance(ev, RuntimeHintGenerated):
                active_hint = ev.text
                hint_counts[ev.question_id] = max(hint_counts.get(ev.question_id, 0), ev.generation_number)
                last_hint = ev.text
            elif isinstance(ev, HintAcknowledged):
                facts = copy.deepcopy(facts)
                facts.hint_interactions.append(
                    {"type": "runtime", "hint_id": ev.hint_id, "question_id": ev.question_id, "quality": ev.quality}
                )
            elif isinstance(ev, SummaryCompressed):
                summary = ev.summary
                unsummarized = unsummarized[ev.count :]
            elif isinstance(ev, Tick) and peer:
                # Time steward: the current question against its budget, once per threshold.
                budget_s = float((question["question_plan"] or {}).get("time_minutes") or 0) * 60
                if question["current_question"] and pace["question_started_at"] and budget_s > 0:
                    elapsed = seconds_between(pace["question_started_at"], ev.at)
                    key = pace_alert(elapsed, budget_s, pace["pace_alerts_sent"])
                    if key is not None:
                        qid = question["active_question_id"] or f"q{question['question_index']}"
                        emit_card(
                            pace_card(
                                f"{session_id}:{qid}:pace:{key}",
                                key,
                                elapsed,
                                budget_s,
                                pace["remaining_questions"],
                                pace["remaining_minutes"],
                                question["active_question_id"] or "",
                                ev.at,
                            )
                        )
                        pace["pace_alerts_sent"] = [*pace["pace_alerts_sent"], key]
                # Stuck countdown: code not moving and the candidate quiet for a while -> one hint per snapshot.
                snapshot_id = str(code_context.get("snapshot_id") or "")
                quiet = seconds_between(last_candidate_at, ev.at)
                if (
                    code_context.get("is_stuck")
                    and snapshot_id
                    and snapshot_id != stuck_hint_for
                    and quiet != float("inf")
                    and quiet >= STUCK_QUIET_S
                ):
                    emit_card(
                        stuck_card(
                            f"{session_id}:{snapshot_id}:stuck", quiet, question["active_question_id"] or "", ev.at
                        )
                    )
                    stuck_hint_for = snapshot_id
                    stuck_hint_due = True
            elif isinstance(ev, CopilotCardUsed):
                if all(c["card_id"] != ev.card_id for c in cards_used):
                    cards_used = [
                        *cards_used,
                        {
                            "card_id": ev.card_id,
                            "kind": ev.kind,
                            "text": ev.text,
                            "question_id": ev.question_id or question["active_question_id"] or "",
                            "at": ev.at,
                        },
                    ][-COPILOT_USAGE_SIZE:]

        return {
            "working_memory": working,
            "unsummarized": unsummarized,
            "rolling_summary": summary,
            "fact_store": facts,
            "total_utterances": total,
            **question,
            "active_hint": active_hint,
            "recent_insights": recent_insights,
            "hint_generation_count": hint_counts,
            "last_runtime_hint": last_hint,
            "copilot_cards_used": cards_used,
            "talk_window": talk_window,
            "last_talk_card_at": last_talk_card_at,
            **pace,
            "last_candidate_at": last_candidate_at,
            "stuck_hint_for": stuck_hint_for,
            "stuck_hint_due": stuck_hint_due,
            "events_processed": state.get("events_processed", 0) + state.get("transport_events", 0),
            "extracted": None,
            "analysis": None,
            "planned_actions": [],
        }

    def dispatch(state: SessionState) -> list[Send] | str:
        sends = [
            Send("extract_facts", ExtractTask(index=i, speaker=ev.speaker, text=ev.text))
            for i, ev in enumerate(state["events"])
            if isinstance(ev, UtteranceIn)
            and ev.text.strip()
            and should_extract_facts(ev.speaker, ev.text, ev.is_final)
        ]
        target = state.get("analyze")
        if target is not None:
            context_text = assemble_context(
                state["working_memory"],
                state["rolling_summary"],
                state["fact_store"],
                question_text=state["current_question"],
                hint_text=state["active_hint"],
            )
            sends.append(
                Send(
                    "analyze",
                    AnalyzeTask(
                        target=target,
                        context_text=context_text,
                        question_plan=state["question_plan"],
                        active_hint=state["active_hint"],
                    ),
                )
            )
        if peer:
            coach = coaching_candidate(state)
            if coach is not None:
                sends.append(Send("coach_interviewer", coach))
            if state.get("stuck_hint_due"):
                sends.append(
                    Send(
                        "draft_hint",
                        HintTask(
                            utterance_id=f"stuck:{state.get('stuck_hint_for', '')}",
                            question_id=state.get("active_question_id") or "",
                            question_plan=state.get("question_plan", {}),
                            working_memory_text=format_utterances(state.get("working_memory", [])),
                            rolling_summary=state.get("rolling_summary", ""),
                            recent_insights=list(state.get("recent_insights", [])),
                        ),
                    )
                )
        return sends or END

    def coaching_candidate(state: SessionState) -> CoachTask | None:
        """The latest substantial interviewer turn of this batch, if the coaching gap has passed."""
        turns = [
            ev
            for ev in state["events"]
            if isinstance(ev, UtteranceIn)
            and ev.is_final
            and ev.speaker == "interviewer"
            and word_count(ev.text) >= COACHING_MIN_WORDS
        ]
        if not turns:
            return None
        latest = turns[-1]
        if seconds_between(state.get("last_coaching_at", ""), latest.timestamp) < COACHING_MIN_GAP_S:
            return None
        return CoachTask(
            utterance_id=latest.id,
            timestamp=latest.timestamp,
            question=state.get("current_question", ""),
            context_text=peer_context(state),
            text=latest.text,
        )

    async def coach_interviewer_node(state: CoachTask) -> dict:
        update = {"last_coaching_at": state["timestamp"]}
        try:
            issues = await asyncio.wait_for(
                retry_transient(
                    lambda: coach_interviewer(
                        failover, session_id, state["context_text"], state["question"], state["text"]
                    )
                ),
                ACTION_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning("peer_action_failed", action="coach_interviewer", session_id=session_id, error=str(e))
            return update
        if issues:
            emit_card(
                copilot_card(
                    f"{session_id}:{state['utterance_id']}:coaching",
                    "coaching",
                    issues[0]["note"],
                    issues[0]["kind"].replace("_", " ").capitalize(),
                    items=[i["suggestion"] for i in issues if i["suggestion"]],
                    priority="info",
                    timestamp=state["timestamp"],
                )
            )
        return update

    async def extract_facts_node(state: ExtractTask) -> dict:
        extracted = await extract_facts(failover, session_id, state["speaker"], state["text"])
        return {"extracted": {state["index"]: extracted}} if extracted else {}

    async def analyze(state: AnalyzeTask) -> dict:
        target = state["target"]
        actions: list[str] = []
        if offered:
            result, actions = await analyze_utterance_with_actions(
                failover,
                session_id,
                state["context_text"],
                state["question_plan"] or None,
                state["active_hint"],
                target.speaker,
                target.text,
                actions=offered,
            )
        else:
            result = await analyze_utterance(
                failover,
                session_id,
                state["context_text"],
                state["question_plan"] or None,
                state["active_hint"],
                target.speaker,
                target.text,
            )
        emit(EFFECT_DASHBOARD, method="send_analysis", payload=result.model_dump())
        emit(
            EFFECT_PERSIST,
            event_type="analysis",
            idempotency_key=f"{session_id}:{target.id}:analysis",
            fields={
                "answer_quality": result.answer_quality,
                "on_track": result.on_track,
                "insight": result.insight,
                "red_flags": result.red_flags if result.red_flags else None,
                "suggestion": result.suggestion_for_interviewer or None,
                "hint_signal": result.hint_signal,
                "dimensions": result.dimensions.model_dump() if result.dimensions else None,
            },
        )
        if result.red_flags:
            emit(
                EFFECT_DASHBOARD,
                method="send_red_flag",
                payload={"flags": result.red_flags, "timestamp": result.timestamp},
            )
        return {"analysis": result, "planned_actions": actions}

    def merge_results(state: SessionState) -> dict:
        update: dict[str, Any] = {}
        extracted = state.get("extracted") or {}
        if extracted:
            facts = copy.deepcopy(state["fact_store"])
            provenance = dict(state.get("claims_by_utterance", {}))
            for index in sorted(extracted):
                source = state["events"][index]
                assert isinstance(source, UtteranceIn)  # only utterances are sent to extract_facts
                provenance[source.id] = facts.merge(extracted[index])
            update["fact_store"] = facts
            update["claims_by_utterance"] = dict(list(provenance.items())[-CLAIM_PROVENANCE_SIZE:])

        result = state.get("analysis")
        if result is not None:
            update |= {
                "analysis_count": state.get("analysis_count", 0) + 1,
                "quality_sum": state.get("quality_sum", 0) + (result.answer_quality if quality_counts(result) else 0),
                "quality_n": state.get("quality_n", 0) + (1 if quality_counts(result) else 0),
                "recent_analyses": (recent := [*state.get("recent_analyses", []), result][-RECENT_ANALYSES:]),
                "problem_solving_score": _problem_solving_score(recent, state.get("problem_solving_score")),
                "recent_insights": [*state.get("recent_insights", []), result][-RECENT_ANALYSES:],
                "analysis_digest": [*state.get("analysis_digest", []), analysis_digest_row(result)][
                    -REPORT_DIGEST_SIZE:
                ],
            }
        return update

    def after_merge(state: SessionState) -> str:
        return "follow_up" if state.get("analysis") is not None else END

    async def follow_up(state: SessionState) -> dict:
        target = state["analyze"]
        result = state["analysis"]
        assert target is not None and result is not None
        found, checked = await check_contradictions(
            failover,
            session_id,
            target.text,
            state["fact_store"],
            state.get("checked_pairs", set()),
            exclude_claims=set(state.get("claims_by_utterance", {}).get(target.id, [])),
        )
        for c in found:
            emit(
                EFFECT_DASHBOARD,
                method="send_red_flag",
                payload={
                    "type": "contradiction",
                    "claim": c.claim,
                    "new_statement": c.new_statement,
                    "explanation": c.explanation,
                    "suggested_probe": c.suggested_probe,
                    "confidence": c.confidence,
                    "timestamp": c.detected_at,
                },
            )
        if result.hint_signal != "none":
            emit(
                EFFECT_DASHBOARD,
                method="send_hint",
                payload={"signal": result.hint_signal, "timestamp": result.timestamp},
            )
        return {
            "contradictions": [*state.get("contradictions", []), *found],
            "checked_pairs": set(state.get("checked_pairs", set())) | checked,
        }

    # ── Agentic follow-ups ─────────────────────────────────────────────

    def emit_contradiction(c: Contradiction) -> None:
        emit(
            EFFECT_DASHBOARD,
            method="send_red_flag",
            payload={
                "type": "contradiction",
                "claim": c.claim,
                "new_statement": c.new_statement,
                "explanation": c.explanation,
                "suggested_probe": c.suggested_probe,
                "confidence": c.confidence,
                "timestamp": c.detected_at,
            },
        )

    def plan_actions(state: SessionState) -> list[Send] | str:
        target, result = state.get("analyze"), state.get("analysis")
        if target is None or result is None:
            return END
        chosen = set(state.get("planned_actions", []))
        # The rule-based contradiction pass always runs (cheap, deterministic);
        # the LLM pass only when the agent chose to check.
        sends = [
            Send(
                "check_contradiction",
                ContradictionTask(
                    statement=target.text,
                    fact_store=state["fact_store"],
                    checked_pairs=state.get("checked_pairs", set()),
                    exclude_claims=state.get("claims_by_utterance", {}).get(target.id, []),
                    use_llm="check_contradiction" in chosen,
                ),
            )
        ]
        if "draft_hint" in chosen:
            sends.append(
                Send(
                    "draft_hint",
                    HintTask(
                        utterance_id=target.id,
                        question_id=state.get("active_question_id") or "",
                        question_plan=state.get("question_plan", {}),
                        working_memory_text=format_utterances(state.get("working_memory", [])),
                        rolling_summary=state.get("rolling_summary", ""),
                        recent_insights=list(state.get("recent_insights", [])),
                    ),
                )
            )
        code = (state.get("code_context") or {}).get("code", "")
        if "inspect_code" in chosen and code.strip():
            sends.append(
                Send(
                    "inspect_code",
                    InspectTask(
                        question=state.get("current_question", ""),
                        statement=target.text,
                        code=code,
                        language=(state.get("code_context") or {}).get("language", "unknown"),
                    ),
                )
            )
        if peer:
            sends.extend(peer_sends(state, target, result, chosen))
        return sends

    # ── Peer interviewer follow-ups ────────────────────────────────────

    def peer_context(state: SessionState) -> str:
        return assemble_context(
            state.get("working_memory", []),
            state.get("rolling_summary", ""),
            state.get("fact_store") or FactStore(),
            question_text=state.get("current_question", ""),
            hint_text=state.get("active_hint", ""),
        )

    def peer_sends(
        state: SessionState, target: UtteranceIn, result: EnhancedAnalysisResult, chosen: set[str]
    ) -> list[Send]:
        """The peer actions to run for this analysis: chosen by it, or due on the evidence it produced."""
        sends: list[Send] = []
        dims = result.dimensions
        context_text = peer_context(state)
        task = PeerUtteranceTask(
            utterance_id=target.id,
            question_id=state.get("active_question_id") or "",
            question_plan=state.get("question_plan", {}),
            context_text=context_text,
            speaker=target.speaker,
            text=target.text,
        )
        # A probe is due when the analysis asked for one, or when it found gaps worth asking about.
        gaps = bool(dims and dims.depth_completeness.key_points_missed)
        if "suggest_probe" in chosen or gaps:
            sends.append(Send("suggest_probes", ProbeTask(**task, analysis=result)))
        if "verify_claim" in chosen:
            sends.append(Send("verify_claim", task))
        # The candidate asking something is checked even if the analysis did not flag it.
        if "clarify" in chosen or looks_like_a_question(target.text):
            sends.append(Send("draft_clarification", task))
        return sends

    async def verify_claim_node(state: PeerUtteranceTask) -> dict:
        try:
            found = await asyncio.wait_for(
                retry_transient(
                    lambda: verify_claim(
                        failover,
                        session_id,
                        state["context_text"],
                        state["question_plan"] or None,
                        state["speaker"],
                        state["text"],
                    )
                ),
                ACTION_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning("peer_action_failed", action="verify_claim", session_id=session_id, error=str(e))
            return {}
        if found is None:
            return {}
        emit_card(
            copilot_card(
                f"{session_id}:{state['utterance_id']}:claim_check",
                "claim_check",
                f"Claim looks {found['verdict']}: {found['claim']}",
                found["correction"],
                probe=found["self_correct_probe"],
                priority="urgent",
                question_id=state["question_id"],
            )
        )
        return {}

    async def draft_clarification_node(state: PeerUtteranceTask) -> dict:
        try:
            found = await asyncio.wait_for(
                retry_transient(
                    lambda: draft_clarification(
                        failover,
                        session_id,
                        state["context_text"],
                        state["question_plan"] or None,
                        state["speaker"],
                        state["text"],
                    )
                ),
                ACTION_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning("peer_action_failed", action="clarify", session_id=session_id, error=str(e))
            return {}
        if found is None:
            return {}
        emit_card(
            copilot_card(
                f"{session_id}:{state['utterance_id']}:clarification",
                "clarification",
                "Candidate needs a clarification",
                found["what_is_unclear"],
                probe=found["reply"],
                question_id=state["question_id"],
            )
        )
        return {}

    async def suggest_probes_node(state: ProbeTask) -> dict:
        try:
            probes = await asyncio.wait_for(
                retry_transient(
                    lambda: suggest_probes(
                        failover,
                        session_id,
                        state["context_text"],
                        state["question_plan"] or None,
                        state["analysis"],
                        state["speaker"],
                        state["text"],
                    )
                ),
                ACTION_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning("peer_action_failed", action="suggest_probe", session_id=session_id, error=str(e))
            return {}
        if not probes:
            return {}
        emit_card(
            copilot_card(
                f"{session_id}:{state['utterance_id']}:probe",
                "probe",
                "Suggested follow-ups",
                probes[0]["targets"] or "Worth digging into",
                items=[p["question"] for p in probes],
                probe=probes[0]["question"],
                question_id=state["question_id"],
            )
        )
        return {}

    async def check_contradiction(state: ContradictionTask) -> dict:
        try:
            found, checked = await asyncio.wait_for(
                retry_transient(
                    lambda: check_contradictions(
                        failover,
                        session_id,
                        state["statement"],
                        state["fact_store"],
                        state["checked_pairs"],
                        exclude_claims=set(state["exclude_claims"]),
                        use_llm=state["use_llm"],
                    )
                ),
                ACTION_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning("agent_action_failed", action="check_contradiction", session_id=session_id, error=str(e))
            return {}
        for c in found:
            emit_contradiction(c)
        return {"found_contradictions": found, "checked_now": checked}

    async def draft_hint(state: HintTask) -> dict:
        insights = state["recent_insights"]
        request = RuntimeHintRequest(question_id=state["question_id"])
        directness = resolve_directness(request, insights)
        user_content = assemble_hint_context(
            request,
            directness,
            state["question_plan"] or None,
            state["working_memory_text"],
            state["rolling_summary"],
            insights,
            None,
        )
        try:
            response = await asyncio.wait_for(
                retry_transient(
                    lambda: generate_runtime_hint(failover, session_id, request, directness, user_content, 0)
                ),
                ACTION_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning("agent_action_failed", action="draft_hint", session_id=session_id, error=str(e))
            return {}
        if response.text.startswith("Hint generation failed"):
            return {}
        emit(
            EFFECT_DASHBOARD,
            method="send_hint_suggestion",
            payload={
                "id": f"{session_id}:{state['utterance_id']}:hint",
                "question_id": state["question_id"],
                "text": response.text,
                "directness": response.directness.value,
            },
        )
        return {}

    async def inspect_code(state: InspectTask) -> dict:
        try:
            mismatch = await asyncio.wait_for(
                retry_transient(
                    lambda: inspect_code_against_statement(
                        failover,
                        session_id,
                        state["question"],
                        state["statement"],
                        state["code"],
                        state["language"],
                    )
                ),
                ACTION_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning("agent_action_failed", action="inspect_code", session_id=session_id, error=str(e))
            return {}
        if mismatch:
            emit(
                EFFECT_DASHBOARD,
                method="send_red_flag",
                payload={
                    "type": "code_mismatch",
                    "statement": state["statement"],
                    "explanation": mismatch["explanation"],
                    "suggested_probe": mismatch["suggested_probe"],
                    "confidence": mismatch["confidence"],
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        return {}

    def finish(state: SessionState) -> dict:
        result = state.get("analysis")
        if result is not None and result.hint_signal != "none":
            emit(
                EFFECT_DASHBOARD,
                method="send_hint",
                payload={"signal": result.hint_signal, "timestamp": result.timestamp},
            )
        return {
            "contradictions": [*state.get("contradictions", []), *(state.get("found_contradictions") or [])],
            "checked_pairs": set(state.get("checked_pairs", set())) | set(state.get("checked_now") or set()),
            "found_contradictions": None,
            "checked_now": None,
        }

    graph = StateGraph(SessionState)
    graph.add_node("apply_events", apply_events)
    graph.add_node("extract_facts", extract_facts_node, input_schema=ExtractTask)
    graph.add_node("analyze", analyze, input_schema=AnalyzeTask)
    graph.add_node("merge_results", merge_results)
    graph.add_edge(START, "apply_events")
    dispatch_targets = ["extract_facts", "analyze", END]
    if peer:
        graph.add_node("coach_interviewer", coach_interviewer_node, input_schema=CoachTask)
        graph.add_edge("coach_interviewer", "merge_results")
        dispatch_targets[2:2] = ["coach_interviewer", "draft_hint"]  # draft_hint: the stuck countdown
    graph.add_conditional_edges("apply_events", dispatch, dispatch_targets)
    graph.add_edge("extract_facts", "merge_results")
    graph.add_edge("analyze", "merge_results")
    if follow_ups:
        actions = ["check_contradiction", "draft_hint", "inspect_code"]
        graph.add_node("check_contradiction", check_contradiction, input_schema=ContradictionTask)
        graph.add_node("draft_hint", draft_hint, input_schema=HintTask)
        graph.add_node("inspect_code", inspect_code, input_schema=InspectTask)
        if peer:
            graph.add_node("suggest_probes", suggest_probes_node, input_schema=ProbeTask)
            graph.add_node("verify_claim", verify_claim_node, input_schema=PeerUtteranceTask)
            graph.add_node("draft_clarification", draft_clarification_node, input_schema=PeerUtteranceTask)
            actions += ["suggest_probes", "verify_claim", "draft_clarification"]
        graph.add_node("finish", finish)
        graph.add_conditional_edges("merge_results", plan_actions, [*actions, END])
        for action in actions:
            graph.add_edge(action, "finish")
        graph.add_edge("finish", END)
    else:
        graph.add_node("follow_up", follow_up)
        graph.add_conditional_edges("merge_results", after_merge, ["follow_up", END])
        graph.add_edge("follow_up", END)
    return graph


# ── Reads over state values (shapes the handlers and reports consume) ─────


def worker_dict(values: dict, pending_transport_events: int = 0, memory_in_flight: int = 0) -> dict:
    """The pipeline's ``to_dict()`` (memory, analysis, contradictions), from graph state values."""
    session_id = values["session_id"]
    return {
        "session_id": session_id,
        "events_processed": values["events_processed"] + pending_transport_events,
        "analyses_run": values["analysis_count"],
        "memory": {
            "session_id": session_id,
            "working_memory": [
                {"speaker": u.speaker, "text": u.text, "timestamp": u.timestamp} for u in values["working_memory"]
            ],
            "rolling_summary": values["rolling_summary"],
            "unsummarized_count": len(values["unsummarized"]) - memory_in_flight,
            "fact_store": values["fact_store"].to_dict(),
            "total_utterances": values["total_utterances"],
        },
        "analysis": {
            "session_id": session_id,
            "analysis_count": values["analysis_count"],
            "average_quality": values["quality_sum"] / values["quality_n"] if values["quality_n"] else 0.0,
            "current_question": values["current_question"],
            "recent_analyses": [a.model_dump() for a in values["recent_analyses"]],
        },
        "contradictions": {
            "contradictions": [
                {
                    "claim": c.claim,
                    "new_statement": c.new_statement,
                    "explanation": c.explanation,
                    "confidence": c.confidence,
                    "suggested_probe": c.suggested_probe,
                    "detection_method": c.detection_method,
                    "detected_at": c.detected_at,
                }
                for c in values["contradictions"]
            ],
        },
    }


def report_snapshot(values: dict) -> dict:
    """Report inputs derived from the session (``SessionPipeline.report_snapshot``)."""
    return {
        "fact_store": values["fact_store"].to_dict(),
        "rolling_summary": values["rolling_summary"],
        "analysis_results": list(values["analysis_digest"]),
        "contradictions": [
            {"explanation": c.explanation, "confidence": c.confidence} for c in values["contradictions"]
        ],
        "copilot_cards_used": list(values.get("copilot_cards_used", [])),
    }


STATE_SNAPSHOT_SCHEMA_VERSION = 1  # the snapshot shape the frontend has always received


def state_snapshot(
    values: dict,
    code_snapshot_count: int,
    hint_history: list[dict],
    pending_transport_events: int = 0,
) -> dict:
    """The session-state snapshot GET /api/sessions/{id}/state serves, from graph state.

    The shape SessionStateService served before this pipeline (the frontend
    reads ``plan_progress.current_question_index``), but current to the last run.
    Fields nothing maintained keep their defaults.
    """
    score = values["problem_solving_score"]
    return {
        "session_id": values["session_id"],
        "schema_version": STATE_SNAPSHOT_SCHEMA_VERSION,
        "transcript_count": values["events_processed"] + pending_transport_events,
        "last_transcript_timestamp": None,
        "analysis_count": values["analysis_count"],
        "code_snapshot_count": code_snapshot_count,
        "hint_history": hint_history,
        "active_question_id": values["active_question_id"],
        "scores": {} if score is None else {"problem_solving": score},
        "coverage_tracker": {},
        "plan_progress": {"current_phase_index": 0, "current_question_index": values["question_index"]},
        "facts": values["fact_store"].to_dict(),
        "rolling_summary": values["rolling_summary"],
        "captured_at": datetime.now(UTC).isoformat(),
    }
