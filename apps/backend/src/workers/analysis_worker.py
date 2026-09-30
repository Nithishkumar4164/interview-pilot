"""Analysis worker — background processor for real-time interview analysis.

Consumes TranscriptEvents from the transcription service and orchestrates:
  1. Memory updates (tier 1/2/3)
  2. Contradiction detection
  3. Real-time analysis
  4. Dashboard push via WebSocket manager

Provides rate limiting to avoid analyzing every interim result.
"""

import asyncio
import contextlib
from collections.abc import Callable

from src.llm.failover import FailoverManager
from src.schemas.hint import RuntimeHintRequest
from src.schemas.transcript import SpeakerRole, TranscriptEventSchema
from src.services.analysis_service import AnalysisService
from src.services.contradiction_service import ContradictionService
from src.services.memory_service import MemoryService
from src.services.persistence_service import safe_enqueue, safe_update_session_state
from src.services.runtime_hint_service import RuntimeHintService
from src.utils.logging import get_logger
from src.websocket.manager import dashboard_manager

logger = get_logger("analysis_worker")

# Rate limiting: minimum seconds between analysis calls
MIN_ANALYSIS_INTERVAL_S = 3.0


class AnalysisWorker:
    """Orchestrates the real-time analysis pipeline for a session.

    Wired as a callback to the TranscriptionService's event system:
        transcription_service.on_event(worker.on_transcript_event)

    Usage:
        worker = AnalysisWorker(session_id, failover_manager)
        transcription_service.on_event(worker.on_transcript_event)
    """

    def __init__(
        self,
        session_id: str,
        failover_manager: FailoverManager,
        get_speaker_snapshot: Callable[[], dict] | None = None,
        min_analysis_interval_s: float = MIN_ANALYSIS_INTERVAL_S,
    ) -> None:
        self.session_id = session_id
        self._failover = failover_manager
        self._get_speaker_snapshot = get_speaker_snapshot

        # Services
        self.memory = MemoryService(session_id, failover_manager)
        self.analysis = AnalysisService(session_id, failover_manager, self.memory)
        self.contradictions = ContradictionService(session_id, failover_manager)
        self.runtime_hints = RuntimeHintService(session_id, failover_manager, self.memory)

        # Rate limiting
        self._min_analysis_interval_s = min_analysis_interval_s
        self._last_analysis_time: float = 0
        self._pending_analysis: bool = False
        self._pending_snapshot: tuple[str, str] | None = None
        self._analysis_lock = asyncio.Lock()

        # Background analysis task tracking
        self._bg_analysis_task: asyncio.Task | None = None

        # Throttle interim transcript pushes (max once per 300ms)
        self._last_interim_push: float = 0
        self._INTERIM_THROTTLE_S = 0.3

        # Transcript history for catch-up on dashboard connect
        self._transcript_history: list[dict] = []

        # Stats
        self._events_processed = 0
        self._analyses_run = 0
        self._running = True

    async def on_transcript_event(self, event: TranscriptEventSchema) -> None:
        """Process a transcript event through the analysis pipeline.

        Called by the TranscriptionService for every transcript event.
        """
        if not self._running:
            return

        self._events_processed += 1
        speaker = event.speaker_role.value

        # Build transcript event dict (used for dashboard + history + DB)
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

        # 1. Push transcripts to dashboard FIRST (never block on LLM).
        #    Finals are always sent immediately.
        #    Interims are throttled (max once per 300ms) to avoid
        #    single-word flickering while keeping the live preview.
        if event.is_final:
            await dashboard_manager.send_transcript(self.session_id, transcript_event)
        else:
            now = asyncio.get_event_loop().time()
            if now - self._last_interim_push >= self._INTERIM_THROTTLE_S:
                self._last_interim_push = now
                await dashboard_manager.send_transcript(self.session_id, transcript_event)

        # Broadcast speaker snapshot when a new speech vector was created
        if event.speaker_changed and self._get_speaker_snapshot is not None:
            snapshot = self._get_speaker_snapshot()
            await dashboard_manager.send_speaker_snapshot(self.session_id, snapshot)

        # 2. Update memory with ALL events (non-blocking — LLM calls are background tasks)
        await self.memory.add_utterance(
            speaker=speaker,
            text=event.text,
            timestamp=event.timestamp,
            is_final=event.is_final,
        )

        # Store final transcripts for catch-up on dashboard reconnect
        if event.is_final:
            self._transcript_history.append(transcript_event)

            # Persist final transcripts to DB
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
            )

        # 3. Only run analysis on final candidate utterances with rate limiting
        if (
            event.is_final
            and event.speaker_role == SpeakerRole.CANDIDATE
            and len(event.text.split()) > 5  # Skip very short utterances
        ):
            self._schedule_analysis(speaker, event.text)

    def _schedule_analysis(self, speaker: str, text: str) -> None:
        """Schedule analysis as a background task with rate limiting."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_analysis_time

        if elapsed < self._min_analysis_interval_s:
            # Always refresh the pending snapshot so the deferred analysis
            # picks up the latest utterance, not the first one that opened
            # the pending window.
            self._pending_snapshot = (speaker, text)
            if not self._pending_analysis:
                self._pending_analysis = True
                delay = self._min_analysis_interval_s - elapsed
                asyncio.get_event_loop().call_later(
                    delay,
                    lambda: asyncio.ensure_future(self._run_pending_analysis()),
                )
            return

        # Fire-and-forget — _analysis_lock prevents overlapping runs
        self._bg_analysis_task = asyncio.create_task(self._run_analysis(speaker, text))

    async def _run_pending_analysis(self) -> None:
        """Consume pending snapshots until none remain.

        The loop closes a race: an utterance arriving after the snapshot
        is read but before ``_run_analysis`` resets ``_pending_analysis``
        under its lock would see the flag still True and skip scheduling
        a new timer, leaving the new utterance orphaned. By re-checking
        after each ``_run_analysis`` completes (which reset the flag as
        its first action), we pick up any such late arrivals.
        """
        while True:
            snapshot = self._pending_snapshot
            self._pending_snapshot = None
            if snapshot is None:
                # Defensive: the flag is reset inside ``_run_analysis``,
                # but a trailing ``_run_pending_analysis`` with no work
                # should still leave the flag clean for the next window.
                self._pending_analysis = False
                return
            speaker, text = snapshot
            await self._run_analysis(speaker, text)

    async def _run_analysis(self, speaker: str, text: str) -> None:
        """Execute the analysis + contradiction pipeline."""
        async with self._analysis_lock:
            self._pending_analysis = False
            self._last_analysis_time = asyncio.get_event_loop().time()
            self._analyses_run += 1

            # Run analysis
            result = await self.analysis.analyze_utterance(speaker, text)

            # Feed insight to runtime hint service
            self.runtime_hints.add_insight(result)

            # Push analysis to dashboard
            await dashboard_manager.send_analysis(self.session_id, result.model_dump())

            # Persist analysis event to DB
            safe_enqueue(
                "analysis",
                self.session_id,
                answer_quality=result.answer_quality,
                on_track=result.on_track,
                insight=result.insight,
                red_flags=result.red_flags if result.red_flags else None,
                suggestion=result.suggestion_for_interviewer or None,
                hint_signal=result.hint_signal,
                dimensions=result.dimensions.model_dump() if result.dimensions else None,
            )

            # Update session state
            safe_update_session_state(self.session_id, self.to_dict())

            # Check for red flags
            if result.red_flags:
                await dashboard_manager.send_red_flag(
                    self.session_id,
                    {
                        "flags": result.red_flags,
                        "timestamp": result.timestamp,
                    },
                )

            # Check for contradictions against known facts
            contradictions = await self.contradictions.check(
                text,
                self.memory.fact_store,
            )
            for c in contradictions:
                await dashboard_manager.send_red_flag(
                    self.session_id,
                    {
                        "type": "contradiction",
                        "claim": c.claim,
                        "new_statement": c.new_statement,
                        "explanation": c.explanation,
                        "suggested_probe": c.suggested_probe,
                        "confidence": c.confidence,
                        "timestamp": c.detected_at,
                    },
                )

            # Check hint signal
            if result.hint_signal != "none":
                await dashboard_manager.send_hint(
                    self.session_id,
                    {
                        "signal": result.hint_signal,
                        "timestamp": result.timestamp,
                    },
                )

    def set_current_question(self, question_text: str) -> None:
        """Update the current question context."""
        self.analysis.set_current_question(question_text)

    def set_active_hint(self, hint_text: str) -> None:
        """Update the active hint."""
        self.analysis.set_active_hint(hint_text)

    def set_question_plan(self, question_data: dict) -> None:
        """Propagate question plan to analysis and runtime hint services."""
        self.analysis.set_question_plan(question_data)
        self.runtime_hints.set_question(question_data)

    async def on_question_change(self, question_text: str, question_plan: dict, question_index: int = 0) -> None:
        """Handle a question navigation event from the dashboard.

        Resets stale context so the analysis pipeline evaluates against the new question.
        """
        logger.info(
            "question_change_started",
            session_id=self.session_id,
            question_index=question_index,
            question_text=question_text[:80],
        )
        # 1. Force-compress + clear Tier 1 working memory
        await self.memory.reset_for_new_question()
        # 2. Update analysis service context
        self.analysis.set_current_question(question_text)
        self.analysis.set_question_plan(question_plan)
        self.analysis.set_active_hint("")
        # 3. Update runtime hint service
        self.runtime_hints.set_question(question_plan)
        self.runtime_hints._recent_insights.clear()
        # 4. Reset rate limiter so first utterance on new question is analyzed immediately
        self._last_analysis_time = 0

        # 5. Update session state with new question context
        try:
            from src.dependencies import get_session_state_service

            sss = get_session_state_service()
            state = sss.get_state(self.session_id)
            state.plan_progress["current_question_index"] = question_index
            state.active_question_id = question_plan.get("id")
            state.mark_dirty()
        except Exception:
            logger.warning("session_state_question_update_failed")

    async def handle_runtime_hint_request(self, request_data: dict) -> dict:
        """Handle a runtime hint generation request."""
        request = RuntimeHintRequest.model_validate(request_data)
        response = await self.runtime_hints.generate_hint(request)
        # Inform analysis service so hint_reception dimension is scored
        self.analysis.set_active_hint(response.text)
        return response.model_dump()

    async def handle_code_rating(self, data: dict) -> dict:
        """Handle a code rating request from the dashboard."""
        code = data.get("code", "")
        language = data.get("language", "unknown")
        question_id = data.get("question_id", "")
        result = await self.analysis.rate_candidate_code(code, language)
        result["question_id"] = question_id
        return result

    async def handle_runtime_hint_ack(self, data: dict) -> None:
        """Handle runtime hint acknowledgment for logging."""
        hint_id = data.get("hint_id", "")
        quality = data.get("quality", "none")
        question_id = data.get("question_id", "")

        # Record in memory service
        self.memory.add_hint_interaction(
            {
                "type": "runtime",
                "hint_id": hint_id,
                "question_id": question_id,
                "quality": quality,
            }
        )

    def get_recent_transcripts(self, limit: int = 50) -> list[dict]:
        """Return recent transcript events for catch-up replay."""
        return self._transcript_history[-limit:]

    def get_transcripts_by_ids(self, transcript_ids: set[str]) -> list[dict]:
        """Return final transcripts whose id is in ``transcript_ids``, in arrival order."""
        return [t for t in self._transcript_history if t["id"] in transcript_ids]

    def report_snapshot(self) -> dict:
        """Session-derived inputs for report generation."""
        return {
            "fact_store": self.memory.fact_store.to_dict(),
            "rolling_summary": self.memory.get_summary(),
            "analysis_results": [
                {
                    "answer_quality": a.answer_quality,
                    "on_track": a.on_track,
                    "insight": a.insight,
                    "red_flags": a.red_flags,
                    "suggestion": a.suggestion_for_interviewer,
                    "hint_signal": a.hint_signal,
                }
                for a in self.analysis.get_recent_analyses(count=50)
            ],
            "contradictions": [
                {"explanation": c.explanation, "confidence": c.confidence} for c in self.contradictions.contradictions
            ],
        }

    async def drain(self) -> None:
        """Flush all in-flight background work for safe shutdown.

        Drains memory service tasks and any in-flight analysis task.
        """
        await self.memory.drain()
        if self._bg_analysis_task and not self._bg_analysis_task.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._bg_analysis_task, timeout=10.0)

    async def wait_idle(self) -> None:
        """Wait until no analysis, deferred analysis or memory task is in flight (replay/tests)."""
        while True:
            pending = [
                t
                for t in (self._bg_analysis_task, self.memory._bg_compress_task, *self.memory._bg_extract_tasks)
                if t is not None and not t.done()
            ]
            if pending:
                await asyncio.wait(pending)
            elif self._pending_analysis or self._analysis_lock.locked():
                await asyncio.sleep(0.01)
            else:
                return

    def stop(self) -> None:
        """Stop the worker."""
        self._running = False

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "events_processed": self._events_processed,
            "analyses_run": self._analyses_run,
            "memory": self.memory.to_dict(),
            "analysis": self.analysis.to_dict(),
            "contradictions": self.contradictions.to_dict(),
        }
