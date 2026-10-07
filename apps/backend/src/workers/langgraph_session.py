"""LangGraphSessionPipeline — the SessionPipeline backed by SessionGraph.

Transport stays here (not in the graph): the dashboard gets every
final transcript immediately and interims at most every 300 ms; speaker
snapshots; the in-process transcript history for dashboard catch-up; the
transcript DB write.

Everything that changes session state goes through one ingress queue and one
consumer task, so exactly one graph run per session is in flight:

- Batching: a run takes everything queued since the previous run.
- Rate limiting: at most one analysis per ``min_analysis_interval_s``
  (reset by a question change). A candidate utterance arriving inside the
  window is remembered, and analyzed when the window ends unless a newer one
  replaces it first — "analyze the latest", as before. A batch is cut right
  after the utterance chosen for analysis, so its analysis sees memory up to
  that utterance, not later ones.
- Effects: nodes stream effects; this class performs them (dashboard pushes,
  idempotent DB writes, session-state updates) as they arrive.
- Memory compression runs as a task beside the graph, one at a time, and
  lands through the queue as a ``SummaryCompressed`` event. The buffer stays
  in graph state until then, so a crash mid-compression loses nothing.
- Durability: runs checkpoint to Postgres (``durability="async"``). The first
  thing a new pipeline does is finish any run its thread left interrupted
  (e.g. by a process crash), then load the state, so memory, facts and
  question context survive a restart. A Postgres advisory lock per run keeps
  a second process holding a pipeline for the same session (after an audio
  reconnect lands elsewhere) from running concurrently.
"""

import asyncio
import contextlib
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Durability

from src.graphs.checkpointer import Checkpointer
from src.graphs.code_graph import (
    EFFECT_CODE_LLM,
    CodeEvent,
    CodeRefined,
    CodeSnapshotIn,
    build_code_graph,
    code_thread_id,
    initial_code_state,
    new_snapshot_id,
)
from src.graphs.session_graph import (
    EFFECT_COMPRESS,
    EFFECT_COMPRESS_AFTER_IN_FLIGHT,
    EFFECT_DASHBOARD,
    EFFECT_PERSIST,
    HintAcknowledged,
    HintRevealed,
    IngressEvent,
    QuestionChanged,
    RuntimeHintGenerated,
    SummaryCompressed,
    UtteranceIn,
    build_session_graph,
    initial_state,
    is_analysis_candidate,
    report_snapshot,
    state_snapshot,
    worker_dict,
)
from src.llm.failover import FailoverManager
from src.schemas.hint import RuntimeHintRequest
from src.schemas.transcript import TranscriptEventSchema
from src.services.analysis_service import rate_code
from src.services.code_analysis_service import code_report_fields, llm_analyze_code
from src.services.memory_service import compress_summary, format_utterances
from src.services.persistence_service import safe_enqueue
from src.services.runtime_hint_service import assemble_hint_context, generate_runtime_hint, resolve_directness
from src.utils.logging import get_logger
from src.websocket.manager import dashboard_manager

logger = get_logger("langgraph_session")

MIN_ANALYSIS_INTERVAL_S = 3.0
INTERIM_THROTTLE_S = 0.3


def thread_id_for(session_id: str) -> str:
    return session_id


class _CodeLane:
    """CodeGraph's queue and consumer: one run at a time, each run takes everything queued.

    Separate from the session lane so editor snapshots never wait behind an
    utterance analysis. Effects go through the owning pipeline.
    """

    def __init__(self, owner: "LangGraphSessionPipeline") -> None:
        self._owner = owner
        self._config: RunnableConfig = {"configurable": {"thread_id": code_thread_id(owner.session_id)}}
        self._graph = build_code_graph(owner.session_id)
        self._app: CompiledStateGraph | None = None
        self._queue: deque[CodeEvent] = deque()
        self._wake = asyncio.Event()
        self._consumer: asyncio.Task | None = None
        self._loaded = asyncio.Event()
        self._running_graph = False
        self._refine_task: asyncio.Task | None = None
        self.values: dict[str, Any] = initial_code_state(owner.session_id)

    def enqueue(self, event: CodeEvent) -> None:
        self._queue.append(event)
        self.ensure_consumer()
        self._wake.set()

    def ensure_consumer(self) -> None:
        if self._consumer is None or self._consumer.done():
            self._consumer = asyncio.create_task(self._consume(), name=f"code-graph:{self._owner.session_id}")

    def busy(self) -> bool:
        refining = self._refine_task is not None and not self._refine_task.done()
        return bool(not self._loaded.is_set() or self._queue or self._running_graph or refining)

    def compiled(self) -> CompiledStateGraph:
        if self._app is None:
            self._app = self._graph.compile(checkpointer=self._owner._checkpointer.saver)
        return self._app

    async def _consume(self) -> None:
        lock_key = f"code:{self._owner.session_id}"
        if not self._loaded.is_set():
            try:
                async with self._owner._checkpointer.advisory_lock(lock_key):
                    snapshot = await self.compiled().aget_state(self._config)
                    if snapshot.next:
                        logger.info("code_run_resuming", session_id=self._owner.session_id)
                        await self._stream(None)
                        snapshot = await self.compiled().aget_state(self._config)
                if snapshot.values:
                    self.values = {**initial_code_state(self._owner.session_id), **snapshot.values}
            except Exception:
                logger.exception("code_resume_failed", session_id=self._owner.session_id)
            finally:
                self._loaded.set()
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._queue:
                events = list(self._queue)
                self._queue.clear()
                self._running_graph = True
                try:
                    async with self._owner._checkpointer.advisory_lock(lock_key):
                        await self._stream({"session_id": self._owner.session_id, "events": events})
                except Exception:
                    logger.exception("code_run_failed", session_id=self._owner.session_id, events=len(events))
                finally:
                    self._running_graph = False

    async def _stream(self, graph_input: dict | None) -> None:
        async for mode, chunk in self.compiled().astream(
            graph_input, self._config, stream_mode=["custom", "values"], durability=self._owner._durability
        ):
            data = cast(dict, chunk)
            if mode == "values":
                self.values = {**initial_code_state(self._owner.session_id), **data}
            elif data["effect"] == EFFECT_CODE_LLM:
                self._start_refine(data)
            else:
                await self._owner._perform(data)

    def _start_refine(self, request: dict) -> None:
        if self._refine_task is not None and not self._refine_task.done():
            return  # one LLM analysis at a time; a later snapshot will qualify again
        self._refine_task = asyncio.create_task(self._refine(request))

    async def _refine(self, request: dict) -> None:
        local = request["local"]
        refined = await llm_analyze_code(
            self._owner._failover,
            self._owner.session_id,
            request["question"],
            request["code"],
            request["language"],
            local.progress_state,
            local.detected_approach,
        )
        if refined is not None:
            self.enqueue(
                CodeRefined(
                    snapshot_id=request["snapshot_id"],
                    result=refined,
                    code_length=request["code_length"],
                    language=request["language"],
                    source=request["source"],
                )
            )

    async def close(self) -> None:
        for task in (self._consumer, self._refine_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        await self._owner._checkpointer.delete_threads(code_thread_id(self._owner.session_id))


class LangGraphSessionPipeline:
    def __init__(
        self,
        session_id: str,
        failover_manager: FailoverManager,
        checkpointer: Checkpointer,
        get_speaker_snapshot: Callable[[], dict] | None = None,
        min_analysis_interval_s: float = MIN_ANALYSIS_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        durability: Durability = "async",
        interim_throttle_s: float = INTERIM_THROTTLE_S,
        agentic: bool | None = None,
    ) -> None:
        self.session_id = session_id
        self._failover = failover_manager
        self._checkpointer = checkpointer
        self._get_speaker_snapshot = get_speaker_snapshot
        self._min_interval = min_analysis_interval_s
        self._clock = clock
        self._durability: Durability = durability
        self._interim_throttle_s = interim_throttle_s
        if agentic is None:
            from src.config import settings

            agentic = settings.agentic_routing_enabled
        self._graph = build_session_graph(failover_manager, session_id, agentic=agentic)
        self._app: CompiledStateGraph | None = None
        self._config: RunnableConfig = {"configurable": {"thread_id": thread_id_for(session_id)}}

        # Transport
        self._last_interim_push = 0.0
        self._transcript_history: list[dict] = []
        self._transport_events = 0  # transcript events not yet handed to a run

        # Ingress
        self._queue: deque[IngressEvent] = deque()
        self._wake = asyncio.Event()
        self._consumer: asyncio.Task | None = None
        self._running_graph = False
        self._running = True

        # Rate limit (process-local; a restarted process analyzes the next candidate at once)
        self._last_analysis_at: float | None = None
        self._pending: UtteranceIn | None = None
        self._pending_timer: asyncio.TimerHandle | None = None

        # Memory compression task
        self._compress_task: asyncio.Task | None = None
        self._compress_in_flight = 0  # buffered utterances the running compression covers
        self._compress_after_in_flight = False

        # Latest state values (updated after every step); reads are served from here.
        self._values: dict[str, Any] = initial_state(session_id)
        self._loaded = asyncio.Event()

        # Editor snapshots run on their own graph thread and queue.
        self._code = _CodeLane(self)

        # Runtime hint numbering must be synchronous so concurrent requests get distinct numbers.
        self._hint_counts: dict[str, int] = {}
        self._last_runtime_hint: str | None = None

    # ── Inbound events ────────────────────────────────────────────────

    async def on_transcript_event(self, event: TranscriptEventSchema) -> None:
        if not self._running:
            return

        self._transport_events += 1
        speaker = event.speaker_role.value
        transcript_event = {
            "id": event.id,
            "speaker_role": speaker,
            "text": event.text,
            "is_final": event.is_final,
            "confidence": event.confidence,
            "timestamp": event.timestamp,
            "channel": event.channel,
            "is_retroactive": event.is_retroactive,
            "root_speaker_id": event.root_speaker_id,
            "speech_vector_id": event.speech_vector_id,
        }

        # Push transcripts to the dashboard first (never wait on the LLM):
        # finals immediately, interims at most every 300 ms.
        if event.is_final:
            await dashboard_manager.send_transcript(self.session_id, transcript_event)
        else:
            now = asyncio.get_running_loop().time()
            if now - self._last_interim_push >= self._interim_throttle_s:
                self._last_interim_push = now
                await dashboard_manager.send_transcript(self.session_id, transcript_event)

        if event.speaker_changed and self._get_speaker_snapshot is not None:
            await dashboard_manager.send_speaker_snapshot(self.session_id, self._get_speaker_snapshot())

        # Interims only preview a sentence that arrives again as a final;
        # they go to the dashboard above, never into memory.
        if event.is_final:
            self._enqueue(
                UtteranceIn(id=event.id, speaker=speaker, text=event.text, timestamp=event.timestamp, is_final=True)
            )
            self._transcript_history.append(transcript_event)
            safe_enqueue(
                "transcript",
                self.session_id,
                speaker_role=speaker,
                text=event.text,
                is_final=True,
                confidence=event.confidence,
                timestamp=event.timestamp,
                channel=event.channel,
                is_retroactive=event.is_retroactive,
                root_speaker_id=event.root_speaker_id,
                speech_vector_id=event.speech_vector_id,
                idempotency_key=f"{self.session_id}:{event.id}:transcript",
            )

    async def on_question_change(self, question_text: str, question_plan: dict, question_index: int = 0) -> None:
        logger.info(
            "question_change_started",
            session_id=self.session_id,
            question_index=question_index,
            question_text=question_text[:80],
        )
        change = QuestionChanged(question_text, question_plan or {}, question_index)
        self._enqueue(change)
        self._code.enqueue(change)

    def set_active_hint(self, hint_text: str) -> None:
        self._enqueue(HintRevealed(hint_text))

    async def handle_runtime_hint_ack(self, data: dict) -> None:
        self._enqueue(
            HintAcknowledged(
                hint_id=data.get("hint_id", ""),
                question_id=data.get("question_id", ""),
                quality=data.get("quality", "none"),
            )
        )

    async def on_code_snapshot(self, code: str, language: str, source: str) -> None:
        if not self._running:
            return
        self._code.enqueue(
            CodeSnapshotIn(
                id=new_snapshot_id(),
                code=code,
                language=language,
                source=source,
                timestamp=datetime.now(UTC).isoformat(),
            )
        )

    # ── Request/response (read state; never wait behind a run) ────────

    async def handle_runtime_hint_request(self, request_data: dict) -> dict:
        await self._ensure_loaded()
        request = RuntimeHintRequest.model_validate(request_data)
        values = self._values
        question_id = request.question_id
        generation_number = self._hint_counts.get(question_id, 0) + 1
        self._hint_counts[question_id] = generation_number

        directness = resolve_directness(request, values["recent_insights"])
        user_content = assemble_hint_context(
            request,
            directness,
            values["question_plan"] or None,
            format_utterances(values["working_memory"]),
            values["rolling_summary"],
            values["recent_insights"],
            self._last_runtime_hint,
        )
        response = await generate_runtime_hint(
            self._failover, self.session_id, request, directness, user_content, generation_number
        )
        self._last_runtime_hint = response.text
        # Analyses from here on score hint_reception against this hint.
        self._enqueue(RuntimeHintGenerated(question_id, generation_number, response.text))
        logger.info(
            "runtime_hint_generated",
            session_id=self.session_id,
            question_id=question_id,
            directness=response.directness.value,
            generation_number=generation_number,
            is_regeneration=request.regenerate,
            latency_ms=response.latency_ms,
        )
        return response.model_dump()

    async def handle_code_rating(self, data: dict) -> dict:
        await self._ensure_loaded()
        values = self._values
        result = await rate_code(
            self._failover,
            self.session_id,
            values["current_question"],
            values["question_plan"] or None,
            data.get("code", ""),
            data.get("language", "unknown"),
        )
        result["question_id"] = data.get("question_id", "")
        return result

    # ── Reads ─────────────────────────────────────────────────────────

    def get_recent_transcripts(self, limit: int = 50) -> list[dict]:
        return self._transcript_history[-limit:]

    def get_transcripts_by_ids(self, transcript_ids: set[str]) -> list[dict]:
        return [t for t in self._transcript_history if t["id"] in transcript_ids]

    def report_snapshot(self) -> dict:
        return {**report_snapshot(self._values), **code_report_fields(self._code.values["last_result"])}

    def to_dict(self) -> dict:
        return worker_dict(self._values, self._transport_events, self._compress_in_flight)

    def state_snapshot(self, hint_history: list[dict] | None = None) -> dict:
        return state_snapshot(
            self._values, self._code.values["snapshot_count"], hint_history or [], self._transport_events
        )

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def wait_idle(self) -> None:
        self._ensure_consumer()
        self._code.ensure_consumer()
        while self._busy() or self._code.busy():
            await asyncio.sleep(0.005)

    async def drain(self) -> None:
        """Finish all queued work, then fold whatever is still unsummarized into the summary."""
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.wait_idle(), timeout=10.0)
        buffer = list(self._values["unsummarized"])
        if not buffer:
            return
        try:
            summary = await compress_summary(self._failover, self.session_id, self._values["rolling_summary"], buffer)
        except Exception as e:
            logger.error("summary_compression_failed", session_id=self.session_id, error=str(e))
            return
        self._enqueue(SummaryCompressed(summary, len(buffer)))
        await self.wait_idle()

    def stop(self) -> None:
        """Stop accepting transcript events. Work already queued still completes."""
        self._running = False

    async def close(self) -> None:
        """The session ended: finish queued work, stop the consumer, delete the session's checkpoints.

        Report generation got its inputs from ``report_snapshot()`` before this
        runs, so the thread is no longer needed.
        """
        self.stop()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.wait_idle(), timeout=5.0)
        self._cancel_pending_timer()
        for task in (self._consumer, self._compress_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        try:
            await self._code.close()
            await self._checkpointer.delete_threads(thread_id_for(self.session_id))
        except Exception:
            logger.exception("session_thread_delete_failed", session_id=self.session_id)

    # ── Ingress internals ─────────────────────────────────────────────

    def _enqueue(self, event: IngressEvent) -> None:
        self._queue.append(event)
        self._ensure_consumer()
        self._wake.set()

    def _ensure_consumer(self) -> None:
        if self._consumer is None or self._consumer.done():
            self._consumer = asyncio.create_task(self._consume(), name=f"session-graph:{self.session_id}")

    async def _ensure_loaded(self) -> None:
        self._ensure_consumer()
        await self._loaded.wait()

    def _busy(self) -> bool:
        return bool(
            not self._loaded.is_set()
            or self._queue
            or self._running_graph
            or self._pending is not None
            or (self._compress_task is not None and not self._compress_task.done())
        )

    def _compiled(self) -> CompiledStateGraph:
        if self._app is None:
            self._app = self._graph.compile(checkpointer=self._checkpointer.saver)
        return self._app

    async def _consume(self) -> None:
        if not self._loaded.is_set():
            try:
                await self._resume_and_load()
            except Exception:
                logger.exception("session_resume_failed", session_id=self.session_id)
            finally:
                self._loaded.set()
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                taken = self._take_batch()
                if taken is None:
                    break
                events, target = taken
                await self._run(events, target)

    async def _resume_and_load(self) -> None:
        """Finish a run this thread left interrupted, then load the session's state."""
        app = self._compiled()
        async with self._checkpointer.advisory_lock(f"session:{self.session_id}"):
            snapshot = await app.aget_state(self._config)
            if snapshot.next:
                logger.info("session_run_resuming", session_id=self.session_id, next=list(snapshot.next))
                await self._stream(None)
                snapshot = await app.aget_state(self._config)
        if snapshot.values:
            self._values = {**initial_state(self.session_id), **snapshot.values}
            self._hint_counts = dict(self._values["hint_generation_count"])
            self._last_runtime_hint = self._values["last_runtime_hint"]

    def _take_batch(self) -> tuple[list[IngressEvent], UtteranceIn | None] | None:
        """Pick the next run's events and the utterance to analyze; None when there is nothing to run."""
        now = self._clock()
        events: list[IngressEvent] = []
        target: UtteranceIn | None = None

        while self._queue:
            ev = self._queue[0]
            if isinstance(ev, QuestionChanged) and self._pending is not None:
                # A candidate still waiting out the window is analyzed against its own
                # question before the switch, not against the next one.
                target = self._pending
                break
            self._queue.popleft()
            events.append(ev)
            if isinstance(ev, QuestionChanged):
                # First answer to a new question is analyzed immediately.
                self._last_analysis_at = None
            if is_analysis_candidate(ev):
                assert isinstance(ev, UtteranceIn)
                if self._window_open(now):
                    target = ev
                    break  # later events run next, after this analysis
                self._pending = ev  # analyze the latest candidate when the window opens

        if target is None and self._pending is not None and self._window_open(now):
            target = self._pending

        if target is not None:
            self._pending = None
            self._last_analysis_at = now
            self._cancel_pending_timer()
        elif self._pending is not None and self._pending_timer is None:
            assert self._last_analysis_at is not None
            delay = self._last_analysis_at + self._min_interval - now
            self._pending_timer = asyncio.get_running_loop().call_later(delay, self._on_window_open)

        if not events and target is None:
            return None
        return events, target

    def _window_open(self, now: float) -> bool:
        return self._last_analysis_at is None or now - self._last_analysis_at >= self._min_interval

    def _on_window_open(self) -> None:
        self._pending_timer = None
        self._wake.set()

    def _cancel_pending_timer(self) -> None:
        if self._pending_timer is not None:
            self._pending_timer.cancel()
            self._pending_timer = None

    async def _run(self, events: list[IngressEvent], target: UtteranceIn | None) -> None:
        graph_input = {
            "session_id": self.session_id,
            "events": events,
            "analyze": target,
            "transport_events": self._transport_events,
            "memory_in_flight": self._compress_in_flight,
            "code_context": {
                "code": self._code.values.get("latest_code", ""),
                "language": self._code.values.get("latest_language", ""),
            },
        }
        self._transport_events = 0
        self._running_graph = True
        try:
            async with self._checkpointer.advisory_lock(f"session:{self.session_id}"):
                memory_effects = await self._stream(graph_input)
        except Exception:
            logger.exception("session_run_failed", session_id=self.session_id, events=len(events))
            return
        finally:
            self._running_graph = False

        if any(isinstance(ev, SummaryCompressed) for ev in events):
            self._compress_in_flight = 0
            if self._compress_after_in_flight:
                self._compress_after_in_flight = False
                self._start_compression(len(self._values["unsummarized"]))
        for effect in memory_effects:
            if effect["effect"] == EFFECT_COMPRESS:
                self._start_compression(effect["upto"])
            elif effect["effect"] == EFFECT_COMPRESS_AFTER_IN_FLIGHT:
                self._compress_after_in_flight = True

    async def _stream(self, graph_input: dict | None) -> list[dict]:
        """Run (or resume) the graph, performing streamed effects; returns the memory effects for after the run."""
        deferred: list[dict] = []
        async for mode, chunk in self._compiled().astream(
            graph_input, self._config, stream_mode=["custom", "values"], durability=self._durability
        ):
            data = cast(dict, chunk)  # both modes yield dicts: state values, or an effect
            if mode == "values":
                self._values = {**initial_state(self.session_id), **data}
            elif data["effect"] in (EFFECT_COMPRESS, EFFECT_COMPRESS_AFTER_IN_FLIGHT):
                deferred.append(data)
            else:
                await self._perform(data)
        return deferred

    async def _perform(self, effect: dict) -> None:
        kind = effect["effect"]
        if kind == EFFECT_DASHBOARD:
            await getattr(dashboard_manager, effect["method"])(self.session_id, effect["payload"])
        elif kind == EFFECT_PERSIST:
            safe_enqueue(
                effect["event_type"],
                self.session_id,
                idempotency_key=effect["idempotency_key"],
                **effect["fields"],
            )

    # ── Memory compression ────────────────────────────────────────────

    def _start_compression(self, upto: int) -> None:
        if self._compress_in_flight or upto <= 0:
            return
        buffer = list(self._values["unsummarized"][:upto])
        self._compress_in_flight = len(buffer)
        self._compress_task = asyncio.create_task(self._compress(self._values["rolling_summary"], buffer))

    async def _compress(self, summary: str, buffer: list) -> None:
        try:
            new_summary = await compress_summary(self._failover, self.session_id, summary, buffer)
        except Exception as e:
            # The buffer is still in graph state; the next trigger retries it.
            logger.error("summary_compression_failed", session_id=self.session_id, error=str(e))
            self._compress_in_flight = 0
            if self._compress_after_in_flight:
                self._compress_after_in_flight = False
                self._start_compression(len(self._values["unsummarized"]))
            return
        self._enqueue(SummaryCompressed(new_summary, len(buffer)))


@dataclass
class CheckpointedSession:
    """A session's state as stored in its checkpoints, for code that does not hold its pipeline."""

    values: dict[str, Any]
    code_values: dict[str, Any]

    def report_snapshot(self) -> dict:
        return {**report_snapshot(self.values), **code_report_fields(self.code_values["last_result"])}

    def state_snapshot(self, hint_history: list[dict] | None = None) -> dict:
        return state_snapshot(self.values, self.code_values["snapshot_count"], hint_history or [])


async def load_checkpointed_session(
    checkpointer: Checkpointer, failover_manager: FailoverManager, session_id: str
) -> CheckpointedSession | None:
    """The session's checkpointed state (works from any process); None if it has none."""
    session_app = build_session_graph(failover_manager, session_id).compile(checkpointer=checkpointer.saver)
    session = await session_app.aget_state({"configurable": {"thread_id": thread_id_for(session_id)}})
    if not session.values:
        return None
    code_app = build_code_graph(session_id).compile(checkpointer=checkpointer.saver)
    code = await code_app.aget_state({"configurable": {"thread_id": code_thread_id(session_id)}})
    return CheckpointedSession(
        values={**initial_state(session_id), **session.values},
        code_values={**initial_code_state(session_id), **(code.values or {})},
    )


async def delete_session_threads(checkpointer: Checkpointer, session_id: str) -> None:
    await checkpointer.delete_threads(thread_id_for(session_id), code_thread_id(session_id))


async def read_state_snapshot(
    checkpointer: Checkpointer, failover_manager: FailoverManager, session_id: str, hint_history: list[dict]
) -> dict | None:
    """The /state snapshot straight from the session's checkpoints (works from any process).

    None when the session has no checkpointed state (never ran, or ended and
    torn down).
    """
    stored = await load_checkpointed_session(checkpointer, failover_manager, session_id)
    return stored.state_snapshot(hint_history) if stored else None
