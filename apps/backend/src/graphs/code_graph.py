"""CodeGraph — live code tracking for one session, as a graph.

Runs on its own thread (``code:{session_id}``) with its own queue in
``LangGraphSessionPipeline``, so editor snapshots are never queued behind an
utterance analysis (an LLM call), and vice versa.

    apply_code ─► END

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
)


@dataclass(frozen=True)
class CodeSnapshotIn:
    """An editor snapshot from the code WebSocket."""

    id: str
    code: str
    language: str
    source: str
    timestamp: str


CodeEvent = CodeSnapshotIn | QuestionChanged


class CodeState(TypedDict, total=False):
    session_id: str
    events: list[CodeEvent]  # run input (replaced every run)
    history: list[CodeSnapshot]  # newest last, code stripped
    last_result: CodeAnalysisResult | None
    snapshot_count: int


def code_thread_id(session_id: str) -> str:
    return f"code:{session_id}"


def initial_code_state(session_id: str) -> dict[str, Any]:
    return {"session_id": session_id, "history": [], "last_result": None, "snapshot_count": 0}


def code_snapshot_row_id(session_id: str, snapshot_id: str) -> str:
    """Deterministic idempotency key for a snapshot's DB row (a resumed run cannot insert it twice)."""
    return f"{session_id}:{snapshot_id}:code_snapshot"


def build_code_graph(session_id: str) -> StateGraph:
    def apply_code(state: CodeState) -> dict:
        emit = get_stream_writer()
        history = list(state.get("history", []))
        last_result = state.get("last_result")
        count = state.get("snapshot_count", 0)

        for ev in state["events"]:
            if isinstance(ev, QuestionChanged):
                # Code tracking is per question: stuck detection must not compare
                # the new question's code with the previous one's.
                history = []
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
            last_result = result
            count += 1

        return {"history": history, "last_result": last_result, "snapshot_count": count}

    graph = StateGraph(CodeState)
    graph.add_node("apply_code", apply_code)
    graph.add_edge(START, "apply_code")
    graph.add_edge("apply_code", END)
    return graph


def new_snapshot_id() -> str:
    return str(uuid.uuid4())
