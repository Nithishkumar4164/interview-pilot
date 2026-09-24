"""Replay scenario fixtures through a real AnalysisWorker, headlessly."""

import asyncio
import contextlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from src.llm.config import TaskProviderMapping
from src.llm.factory import ProviderFactory
from src.llm.failover import FailoverManager
from src.llm.health import HealthMonitor
from src.llm.interface import TaskType
from src.replay.collector import CapturedEvent, EventCollector
from src.replay.scenario import (
    CodeSnapshotEvent,
    HintAckEvent,
    HintRequestEvent,
    QuestionChangeEvent,
    Scenario,
    TranscriptEvent,
)
from src.replay.scripted_provider import LLMCallRecorder, ScriptedProvider, recording
from src.schemas.transcript import TranscriptEventSchema
from src.workers.analysis_worker import AnalysisWorker

LLMMode = Literal["mock", "scripted", "real"]

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
            "error": self.error,
        }


class ReplayRunner:
    """Replays a Scenario through a real AnalysisWorker.

    Usage:
        runner = ReplayRunner(scenario, llm_mode="mock")
        report = await runner.run()
    """

    def __init__(
        self,
        scenario: Scenario,
        llm_mode: LLMMode = "mock",
        real_time: bool = False,
    ) -> None:
        self.scenario = scenario
        self.llm_mode = llm_mode
        self.real_time = real_time

    async def run(self) -> ReplayReport:
        started_at = datetime.now(UTC).isoformat()
        session_id = self.scenario.meta.id

        if self.llm_mode == "mock":
            fm = build_mock_failover_manager()
        elif self.llm_mode == "scripted":
            fm = build_scripted_failover_manager()
        else:
            fm = _real_failover_manager()

        worker = AnalysisWorker(
            session_id=session_id,
            failover_manager=fm,
            get_speaker_snapshot=lambda: {},
        )

        hint_responses: list[dict] = []
        code_ratings: list[dict] = []
        error: str | None = None
        recorder = LLMCallRecorder()
        scenario_clock = REPLAY_EPOCH

        with recording(recorder):
            async with EventCollector() as collector:
                error = await self._drive(worker, session_id, scenario_clock, hint_responses, code_ratings)

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
            final_state=worker.to_dict(),
            error=error,
        )

    async def _drive(
        self,
        worker: AnalysisWorker,
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
                    # Reset rate limiter so every utterance is analyzed
                    # in compressed-time mode (matches the pattern used
                    # by tests/test_memory_analysis.py).
                    if not self.real_time:
                        worker._last_analysis_time = 0
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
                    # Wait for fire-and-forget analysis task to finish
                    # so its dashboard events are collected before the
                    # next event runs.
                    if worker._bg_analysis_task is not None:
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(worker._bg_analysis_task, timeout=30.0)
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
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            await worker.drain()
        return error


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
