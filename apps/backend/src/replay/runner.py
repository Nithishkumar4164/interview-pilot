"""Replay scenario fixtures through a real session pipeline, headlessly."""

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from src.llm.config import TaskProviderMapping
from src.llm.factory import ProviderFactory
from src.llm.failover import FailoverManager
from src.llm.health import HealthMonitor
from src.llm.interface import TaskType
from src.replay.collector import CapturedEvent, EventCollector, PersistedWrite
from src.replay.scenario import (
    ActiveHintEvent,
    CodeSnapshotEvent,
    HintAckEvent,
    HintRequestEvent,
    QuestionChangeEvent,
    Scenario,
    TranscriptEvent,
)
from src.replay.scripted_provider import LLMCallRecorder, ScriptedProvider, behavior, recording
from src.schemas.transcript import TranscriptEventSchema
from src.workers.analysis_worker import MIN_ANALYSIS_INTERVAL_S, AnalysisWorker
from src.workers.session_pipeline import SessionPipeline

LLMMode = Literal["mock", "scripted", "real"]


@dataclass(frozen=True)
class PipelineTiming:
    """Wall-clock behavior a replay sets on the pipeline under test."""

    min_analysis_interval_s: float
    interim_throttle_s: float


# (session_id, failover_manager, timing) -> pipeline
PipelineFactory = Callable[[str, FailoverManager, PipelineTiming], SessionPipeline]

# Scripted-LLM latency per call site. Calls issued together finish in this
# order, as they tend to against a real provider: a fact extraction started
# alongside an utterance analysis completes first; a summary compression
# outlasts the analysis it was triggered next to.
REPLAY_LATENCY_MS: dict[str, float] = {
    "fact_extraction": 10,
    "contradiction": 10,
    "hint": 20,
    "code_rating": 20,
    "utterance_analysis": 30,
    "summary": 60,
}

# Replay timestamps are derived from this epoch plus each event's delay_ms,
# so recorded outputs don't depend on wall-clock time.
REPLAY_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
_REPLAY_ID_NAMESPACE = uuid.UUID("6f1c2a1e-6a7e-4c1b-9a53-2f4b7f0f2d10")


def _build_single_provider_failover_manager(provider: str, model: str) -> FailoverManager:
    fm = FailoverManager(HealthMonitor())
    for task_type in TaskType:
        fm.update_mapping(
            TaskProviderMapping(
                task_type=task_type,
                primary_provider=provider,
                primary_model=model,
                fallback_provider=provider,
                fallback_model=model,
            )
        )
    return fm


def build_mock_failover_manager() -> FailoverManager:
    """FailoverManager with every TaskType routed to MockProvider.

    Lifted from tests/test_e2e_pipeline.py:29-43 so production-side replay
    and tests share one source.
    """
    return _build_single_provider_failover_manager("mock", "mock-v1")


def build_scripted_failover_manager() -> FailoverManager:
    """FailoverManager with every TaskType routed to ScriptedProvider."""
    ProviderFactory.register("scripted", ScriptedProvider)
    return _build_single_provider_failover_manager("scripted", "scripted-v1")


def _real_failover_manager() -> FailoverManager:
    from src.dependencies import failover_manager

    return failover_manager


def analysis_worker_factory(session_id: str, fm: FailoverManager, timing: PipelineTiming) -> SessionPipeline:
    return AnalysisWorker(
        session_id,
        fm,
        get_speaker_snapshot=lambda: {},
        min_analysis_interval_s=timing.min_analysis_interval_s,
        interim_throttle_s=timing.interim_throttle_s,
    )


@dataclass
class ReplaySummary:
    transcripts: int = 0
    analyses: int = 0
    red_flags: int = 0
    hints: int = 0
    contradictions: int = 0


@dataclass
class ReplayReport:
    scenario_id: str
    llm_mode: str
    started_at: str
    finished_at: str
    summary: ReplaySummary
    events: list[CapturedEvent]
    hint_responses: list[dict] = field(default_factory=list)
    code_ratings: list[dict] = field(default_factory=list)
    llm_calls: list[dict] = field(default_factory=list)
    final_state: dict = field(default_factory=dict)
    persisted: list[PersistedWrite] = field(default_factory=list)
    session_state: dict = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "llm_mode": self.llm_mode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": asdict(self.summary),
            "events": [
                {
                    "method": e.method,
                    "session_id": e.session_id,
                    "payload": e.payload,
                    "timestamp": e.timestamp,
                }
                for e in self.events
            ],
            "hint_responses": self.hint_responses,
            "code_ratings": self.code_ratings,
            "llm_calls": self.llm_calls,
            "final_state": self.final_state,
            "persisted": [asdict(w) for w in self.persisted],
            "session_state": self.session_state,
            "error": self.error,
        }


class ReplayRunner:
    """Replays a Scenario through a session pipeline (AnalysisWorker by default).

    Compressed-time mode (the default) analyzes every qualifying utterance
    (rate-limit interval 0), pushes every interim transcript (throttle 0) and
    waits for the pipeline to go idle after each event, as if the scenario's
    utterances were seconds apart; nothing depends on wall-clock time.
    ``real_time`` honors ``delay_ms``, the production rate limit and the
    300 ms interim throttle instead.

    Usage:
        runner = ReplayRunner(scenario, llm_mode="mock")
        report = await runner.run()
    """

    def __init__(
        self,
        scenario: Scenario,
        llm_mode: LLMMode = "mock",
        real_time: bool = False,
        pipeline_factory: PipelineFactory = analysis_worker_factory,
        latency_ms: dict[str, float] | None = None,
        min_analysis_interval_s: float | None = None,
    ) -> None:
        self.scenario = scenario
        self.llm_mode = llm_mode
        self.real_time = real_time
        self.pipeline_factory = pipeline_factory
        self.latency_ms = REPLAY_LATENCY_MS if latency_ms is None else latency_ms
        if min_analysis_interval_s is None:
            min_analysis_interval_s = MIN_ANALYSIS_INTERVAL_S if real_time else 0.0
        self.min_analysis_interval_s = min_analysis_interval_s

    async def run(self) -> ReplayReport:
        started_at = datetime.now(UTC).isoformat()
        session_id = self.scenario.meta.id

        if self.llm_mode == "mock":
            fm = build_mock_failover_manager()
        elif self.llm_mode == "scripted":
            fm = build_scripted_failover_manager()
        else:
            fm = _real_failover_manager()

        hint_responses: list[dict] = []
        code_ratings: list[dict] = []
        error: str | None = None
        recorder = LLMCallRecorder()
        scenario_clock = REPLAY_EPOCH
        latency = behavior(self.latency_ms) if self.llm_mode == "scripted" else contextlib.nullcontext()

        with recording(recorder), latency:
            async with EventCollector() as collector:
                timing = PipelineTiming(
                    min_analysis_interval_s=self.min_analysis_interval_s,
                    interim_throttle_s=0.3 if self.real_time else 0.0,
                )
                worker = self.pipeline_factory(session_id, fm, timing)
                try:
                    error = await self._drive(worker, session_id, scenario_clock, hint_responses, code_ratings)
                    final_state = worker.to_dict()
                finally:
                    worker.stop()
                    await worker.wait_idle()

        summary = _summarize(collector.events)
        finished_at = datetime.now(UTC).isoformat()

        return ReplayReport(
            scenario_id=session_id,
            llm_mode=self.llm_mode,
            started_at=started_at,
            finished_at=finished_at,
            summary=summary,
            events=list(collector.events),
            hint_responses=hint_responses,
            code_ratings=code_ratings,
            llm_calls=recorder.to_list(),
            final_state=final_state,
            persisted=list(collector.persisted),
            session_state=_session_state(collector, session_id),
            error=error,
        )

    async def _drive(
        self,
        worker: SessionPipeline,
        session_id: str,
        scenario_clock: datetime,
        hint_responses: list[dict],
        code_ratings: list[dict],
    ) -> str | None:
        error: str | None = None
        try:
            for index, ev in enumerate(self.scenario.events):
                scenario_clock += timedelta(milliseconds=ev.delay_ms)
                if self.real_time and ev.delay_ms > 0:
                    await asyncio.sleep(ev.delay_ms / 1000.0)

                if isinstance(ev, QuestionChangeEvent):
                    await worker.on_question_change(
                        ev.question_text,
                        ev.question_plan,
                        ev.question_index,
                    )
                elif isinstance(ev, TranscriptEvent):
                    transcript = TranscriptEventSchema(
                        id=str(uuid.uuid5(_REPLAY_ID_NAMESPACE, f"{session_id}:{index}")),
                        session_id=session_id,
                        speaker_role=ev.speaker_role,
                        text=ev.text,
                        is_final=ev.is_final,
                        confidence=ev.confidence,
                        timestamp=scenario_clock.isoformat(),
                        channel=ev.channel,
                    )
                    await worker.on_transcript_event(transcript)
                elif isinstance(ev, HintRequestEvent):
                    payload = ev.model_dump(exclude={"type", "delay_ms"}, exclude_none=True)
                    response = await worker.handle_runtime_hint_request(payload)
                    hint_responses.append(response)
                elif isinstance(ev, HintAckEvent):
                    await worker.handle_runtime_hint_ack(
                        {
                            "hint_id": ev.hint_id,
                            "question_id": ev.question_id,
                            "quality": ev.quality.value,
                        }
                    )
                elif isinstance(ev, CodeSnapshotEvent):
                    rating = await worker.handle_code_rating(
                        {
                            "code": ev.code,
                            "language": ev.language,
                            "question_id": ev.question_id,
                        }
                    )
                    code_ratings.append(rating)
                elif isinstance(ev, ActiveHintEvent):
                    worker.set_active_hint(ev.text)

                if not self.real_time:
                    await asyncio.wait_for(worker.wait_idle(), timeout=30.0)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            await worker.drain()
        return error


def _session_state(collector: EventCollector, session_id: str) -> dict:
    state = collector.session_state.get_state_or_none(session_id)
    if state is None:
        return {}
    snapshot = state.to_snapshot()
    snapshot.pop("captured_at")
    return snapshot


def _summarize(events: list[CapturedEvent]) -> ReplaySummary:
    summary = ReplaySummary()
    for e in events:
        if e.method == "send_transcript":
            summary.transcripts += 1
        elif e.method == "send_analysis":
            summary.analyses += 1
        elif e.method == "send_red_flag":
            summary.red_flags += 1
            payload = e.payload if isinstance(e.payload, dict) else {}
            if payload.get("type") == "contradiction":
                summary.contradictions += 1
        elif e.method == "send_hint":
            summary.hints += 1
    return summary
