"""CodeGraph — live code tracking for one session, as a graph.

Runs on its own thread (``code:{session_id}``) with its own queue in
``LangGraphSessionPipeline``, so editor snapshots are never queued behind an
utterance analysis (an LLM call), and vice versa.

    apply_code ─► END

LLM refinement runs beside the graph: apply_code emits ``code_llm`` for a
snapshot that warrants it, the pipeline runs the call (one at a time), and
the result returns as a ``CodeRefined`` event, applied only if no newer
snapshot has arrived.

- apply_code: for each editor snapshot in the batch, in order: normalize,
  add it to the bounded history, run the local heuristics (progress,
  approach keywords, stuck detection), push the result to the dashboard and
  persist the snapshot. A question change starts the history over.

Like SessionGraph, the node only emits effects; the pipeline performs them.
The history keeps sizes only (not code), so checkpoints stay small.
"""

import dataclasses
import uuid
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from src.graphs.session_graph import EFFECT_DASHBOARD, EFFECT_PERSIST, QuestionChanged
from src.services.code_analysis_service import (
    MAX_SNAPSHOT_HISTORY,
    CodeAnalysisResult,
    CodeSnapshot,
    analyze_locally,
    make_snapshot,
    should_run_llm_analysis,
)


@dataclass(frozen=True)
class CodeSnapshotIn:
    """An editor snapshot from the code WebSocket."""

    id: str
    code: str
    language: str
    source: str
    timestamp: str


@dataclass(frozen=True)
class CodeRefined:
    """The LLM's analysis of snapshot ``snapshot_id`` (from the background task)."""

    snapshot_id: str
    result: CodeAnalysisResult
    code_length: int
    language: str
    source: str


CodeEvent = CodeSnapshotIn | QuestionChanged | CodeRefined

# {"snapshot_id", "question", "code", "language", "source", "code_length", "local": CodeAnalysisResult}
# -> the pipeline runs llm_analyze_code off the graph and feeds back CodeRefined.
EFFECT_CODE_LLM = "code_llm"


class CodeState(TypedDict, total=False):
    session_id: str
    events: list[CodeEvent]  # run input (replaced every run)
    history: list[CodeSnapshot]  # newest last, code stripped
    question: str  # the current question's text (the LLM step needs it)
    latest_snapshot_id: str | None
    last_result: CodeAnalysisResult | None
    snapshot_count: int


def code_thread_id(session_id: str) -> str:
    return f"code:{session_id}"


def initial_code_state(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "history": [],
        "question": "",
        "latest_snapshot_id": None,
        "last_result": None,
        "snapshot_count": 0,
    }


def code_snapshot_row_id(session_id: str, snapshot_id: str) -> str:
    """Deterministic idempotency key for a snapshot's DB row (a resumed run cannot insert it twice)."""
    return f"{session_id}:{snapshot_id}:code_snapshot"


def build_code_graph(session_id: str) -> StateGraph:
    def apply_code(state: CodeState) -> dict:
        emit = get_stream_writer()
        history = list(state.get("history", []))
        question = state.get("question", "")
        latest = state.get("latest_snapshot_id")
        last_result = state.get("last_result")
        count = state.get("snapshot_count", 0)

        for ev in state["events"]:
            if isinstance(ev, QuestionChanged):
                # Code tracking is per question: stuck detection must not compare
                # the new question's code with the previous one's.
                history = []
                question = ev.question_text
                continue
            if isinstance(ev, CodeRefined):
                if ev.snapshot_id != latest:
                    continue  # newer code arrived while the LLM ran; its result is stale
                refined = dataclasses.replace(ev.result, is_stuck=last_result.is_stuck if last_result else False)
                emit(
                    {
                        "effect": EFFECT_DASHBOARD,
                        "method": "send_code_analysis",
                        "payload": {
                            "code_length": ev.code_length,
                            "language": ev.language,
                            "source": ev.source,
                            **refined.to_dict(),
                        },
                    }
                )
                last_result = refined
                continue
            snapshot = make_snapshot(ev.code, ev.language)
            history = [*history, dataclasses.replace(snapshot, code="")][-MAX_SNAPSHOT_HISTORY:]
            result = analyze_locally(snapshot, history)
            emit(
                {
                    "effect": EFFECT_DASHBOARD,
                    "method": "send_code_analysis",
                    "payload": {
                        "code_length": len(ev.code),
                        "language": ev.language,
                        "source": ev.source,
                        **result.to_dict(),
                    },
                }
            )
            emit(
                {
                    "effect": EFFECT_PERSIST,
                    "event_type": "code_snapshot",
                    "idempotency_key": code_snapshot_row_id(session_id, ev.id),
                    "fields": {
                        "code": ev.code,
                        "language": ev.language,
                        "timestamp": ev.timestamp,
                        "approach": result.detected_approach.name if result.detected_approach else None,
                        "progress_state": result.progress_state.value,
                    },
                }
            )
            if should_run_llm_analysis(question, history):
                emit(
                    {
                        "effect": EFFECT_CODE_LLM,
                        "snapshot_id": ev.id,
                        "question": question,
                        "code": snapshot.code,
                        "language": ev.language,
                        "source": ev.source,
                        "code_length": len(ev.code),
                        "local": result,
                    }
                )
            last_result = result
            latest = ev.id
            count += 1

        return {
            "history": history,
            "question": question,
            "latest_snapshot_id": latest,
            "last_result": last_result,
            "snapshot_count": count,
        }

    graph = StateGraph(CodeState)
    graph.add_node("apply_code", apply_code)
    graph.add_edge(START, "apply_code")
    graph.add_edge("apply_code", END)
    return graph


def new_snapshot_id() -> str:
    return str(uuid.uuid4())
