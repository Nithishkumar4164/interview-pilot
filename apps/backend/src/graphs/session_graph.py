"""SessionGraph — the real-time interview pipeline for one session, as a graph.

Each run processes one batch of ingress events on thread ``session_id``;
``LangGraphSessionPipeline`` (src/workers/langgraph_session.py) queues events,
chooses the batch and the utterance to analyze, and runs the graph.

    apply_events ─┬─► Send(extract_facts) × long final utterances ─┐
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

import copy
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from src.llm.failover import FailoverManager
from src.schemas.analysis import EnhancedAnalysisResult
from src.services.analysis_service import analyze_utterance
from src.services.contradiction_service import Contradiction, check_contradictions
from src.services.memory_service import (
    SUMMARY_TRIGGER_COUNT,
    WORKING_MEMORY_SIZE,
    FactStore,
    Utterance,
    assemble_context,
    extract_facts,
    should_extract_facts,
)

RECENT_ANALYSES = 5  # AnalysisService.to_dict / RuntimeHintService insight window
REPORT_DIGEST_SIZE = 50  # report_snapshot's analysis_results window

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


IngressEvent = (
    UtteranceIn | QuestionChanged | HintRevealed | HintAcknowledged | RuntimeHintGenerated | SummaryCompressed
)

# ── Effects (custom stream payloads) ──────────────────────────────────────

EFFECT_DASHBOARD = "dashboard"  # {"method", "payload"} -> dashboard_manager.<method>(session_id, payload)
EFFECT_PERSIST = "persist"  # {"event_type", "fields", "idempotency_key"} -> safe_enqueue
EFFECT_COMPRESS = "compress"  # {"upto": n}: compress unsummarized[:n] now
EFFECT_COMPRESS_AFTER_IN_FLIGHT = "compress_after_in_flight"  # question change while a compression runs
EFFECT_QUESTION_CHANGED = "question_changed"  # {"question_index", "question_id"} -> session-state service


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
    events_processed: int

    # ── contradictions ──
    contradictions: list[Contradiction]
    checked_pairs: set[str]

    # ── runtime hints ──
    hint_generation_count: dict[str, int]
    last_runtime_hint: str | None

    # ── per-run scratch ──
    extracted: Annotated[dict[int, dict], _reset_or_merge]
    analysis: EnhancedAnalysisResult | None


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
        "events_processed": 0,
        "contradictions": [],
        "checked_pairs": set(),
        "hint_generation_count": {},
        "last_runtime_hint": None,
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


class ExtractTask(TypedDict):
    index: int
    speaker: str
    text: str


class AnalyzeTask(TypedDict):
    target: UtteranceIn
    context_text: str
    question_plan: dict
    active_hint: str


def build_session_graph(failover: FailoverManager, session_id: str) -> StateGraph:
    """The graph for one session (``session_id`` scopes LLM calls and effect keys)."""

    def emit(kind: str, **data: Any) -> None:
        get_stream_writer()({"effect": kind, **data})

    def apply_events(state: SessionState) -> dict:
        working = list(state.get("working_memory", []))
        unsummarized = list(state.get("unsummarized", []))
        summary = state.get("rolling_summary", "")
        facts = state.get("fact_store") or FactStore()
        total = state.get("total_utterances", 0)
        question = {
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
                emit(
                    EFFECT_QUESTION_CHANGED,
                    question_index=ev.question_index,
                    question_id=ev.question_plan.get("id"),
                )
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
            "events_processed": state.get("events_processed", 0) + state.get("transport_events", 0),
            "extracted": None,
            "analysis": None,
        }

    def dispatch(state: SessionState) -> list[Send] | str:
        sends = [
            Send("extract_facts", ExtractTask(index=i, speaker=ev.speaker, text=ev.text))
            for i, ev in enumerate(state["events"])
            if isinstance(ev, UtteranceIn) and ev.text.strip() and should_extract_facts(ev.text, ev.is_final)
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
        return sends or END

    async def extract_facts_node(state: ExtractTask) -> dict:
        extracted = await extract_facts(failover, session_id, state["speaker"], state["text"])
        return {"extracted": {state["index"]: extracted}} if extracted else {}

    async def analyze(state: AnalyzeTask) -> dict:
        target = state["target"]
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
        return {"analysis": result}

    def merge_results(state: SessionState) -> dict:
        update: dict[str, Any] = {}
        extracted = state.get("extracted") or {}
        if extracted:
            facts = copy.deepcopy(state["fact_store"])
            for index in sorted(extracted):
                facts.merge(extracted[index])
            update["fact_store"] = facts

        result = state.get("analysis")
        if result is not None:
            update |= {
                "analysis_count": state.get("analysis_count", 0) + 1,
                "quality_sum": state.get("quality_sum", 0) + (result.answer_quality if quality_counts(result) else 0),
                "quality_n": state.get("quality_n", 0) + (1 if quality_counts(result) else 0),
                "recent_analyses": [*state.get("recent_analyses", []), result][-RECENT_ANALYSES:],
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
            failover, session_id, target.text, state["fact_store"], state.get("checked_pairs", set())
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

    graph = StateGraph(SessionState)
    graph.add_node("apply_events", apply_events)
    graph.add_node("extract_facts", extract_facts_node, input_schema=ExtractTask)
    graph.add_node("analyze", analyze, input_schema=AnalyzeTask)
    graph.add_node("merge_results", merge_results)
    graph.add_node("follow_up", follow_up)
    graph.add_edge(START, "apply_events")
    graph.add_conditional_edges("apply_events", dispatch, ["extract_facts", "analyze", END])
    graph.add_edge("extract_facts", "merge_results")
    graph.add_edge("analyze", "merge_results")
    graph.add_conditional_edges("merge_results", after_merge, ["follow_up", END])
    graph.add_edge("follow_up", END)
    return graph


# ── Reads over state values (the shapes the old AnalysisWorker exposed) ────


def worker_dict(values: dict, pending_transport_events: int = 0, memory_in_flight: int = 0) -> dict:
    """``AnalysisWorker.to_dict()`` shape, from graph state values."""
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
    """``AnalysisWorker.report_snapshot()`` shape, from graph state values."""
    return {
        "fact_store": values["fact_store"].to_dict(),
        "rolling_summary": values["rolling_summary"],
        "analysis_results": list(values["analysis_digest"]),
        "contradictions": [
            {"explanation": c.explanation, "confidence": c.confidence} for c in values["contradictions"]
        ],
    }
