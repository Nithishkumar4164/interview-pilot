"""Analysis worker — background processor for real-time interview analysis.

Consumes TranscriptEvents from the transcription service and orchestrates:
  1. Memory updates (tier 1/2/3)
  2. Contradiction detection
  3. Real-time analysis
  4. Dashboard push via WebSocket manager

Provides rate limiting to avoid analyzing every interim result.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from src.llm.failover import FailoverManager
from src.schemas.transcript import SpeakerRole, TranscriptEventSchema
from src.schemas.hint import RuntimeHintRequest
from src.services.analysis_service import AnalysisService
from src.services.contradiction_service import ContradictionService
from src.services.memory_service import MemoryService
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
        self._last_analysis_time: float = 0
        self._pending_analysis: bool = False
        self._analysis_lock = asyncio.Lock()

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

        # 1. Always update memory (even for interim results)
        await self.memory.add_utterance(
            speaker=speaker,
            text=event.text,
            timestamp=event.timestamp,
            is_final=event.is_final,
        )

        # 2. Push transcript to dashboard
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
        await dashboard_manager.send_transcript(self.session_id, transcript_event)

        # Broadcast speaker snapshot when a new speech vector was created
        if event.speaker_changed and self._get_speaker_snapshot is not None:
            snapshot = self._get_speaker_snapshot()
            await dashboard_manager.send_speaker_snapshot(self.session_id, snapshot)

        # Store final transcripts for catch-up on dashboard reconnect
        if event.is_final:
            self._transcript_history.append(transcript_event)

            # Persist final transcripts to DB
            try:
                from src.dependencies import get_persistence_service
                persistence = get_persistence_service()
                persistence.enqueue_transcript(
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
            except Exception:
                logger.warning("persistence_enqueue_failed", type="transcript")

        # 3. Only run analysis on final candidate utterances with rate limiting
        if (
            event.is_final
            and event.speaker_role == SpeakerRole.CANDIDATE
            and len(event.text.split()) > 5  # Skip very short utterances
        ):
            await self._maybe_analyze(speaker, event.text)

    async def _maybe_analyze(self, speaker: str, text: str) -> None:
        """Run analysis with rate limiting."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_analysis_time

        if elapsed < MIN_ANALYSIS_INTERVAL_S:
            # Too soon — mark pending and schedule
            if not self._pending_analysis:
                self._pending_analysis = True
                delay = MIN_ANALYSIS_INTERVAL_S - elapsed
                asyncio.get_event_loop().call_later(
                    delay,
                    lambda: asyncio.ensure_future(
                        self._run_analysis(speaker, text)
                    ),
                )
            return

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
            try:
                from src.dependencies import get_persistence_service
                persistence = get_persistence_service()
                persistence.enqueue_analysis(
                    self.session_id,
                    answer_quality=result.answer_quality,
                    on_track=result.on_track,
                    insight=result.insight,
                    red_flags=result.red_flags if result.red_flags else None,
                    suggestion=result.suggestion_for_interviewer or None,
                    hint_signal=result.hint_signal,
                )
            except Exception:
                logger.warning("persistence_enqueue_failed", type="analysis")

            # Update session state
            try:
                from src.dependencies import get_session_state_service
                sss = get_session_state_service()
                sss.update_from_worker(self.session_id, self.to_dict())
            except Exception:
                logger.warning("session_state_update_failed")

            # Check for red flags
            if result.red_flags:
                await dashboard_manager.send_red_flag(self.session_id, {
                    "flags": result.red_flags,
                    "timestamp": result.timestamp,
                })

            # Check for contradictions against known facts
            contradictions = await self.contradictions.check(
                text, self.memory.fact_store,
            )
            for c in contradictions:
                await dashboard_manager.send_red_flag(self.session_id, {
                    "type": "contradiction",
                    "claim": c.claim,
                    "new_statement": c.new_statement,
                    "explanation": c.explanation,
                    "suggested_probe": c.suggested_probe,
                    "confidence": c.confidence,
                    "timestamp": c.detected_at,
                })

            # Check hint signal
            if result.hint_signal != "none":
                await dashboard_manager.send_hint(self.session_id, {
                    "signal": result.hint_signal,
                    "timestamp": result.timestamp,
                })

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

    async def on_question_change(
        self, question_text: str, question_plan: dict, question_index: int = 0
    ) -> None:
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
        text = data.get("text", "")

        # Record in memory service
        self.memory.add_hint_interaction({
            "type": "runtime",
            "hint_id": hint_id,
            "question_id": question_id,
            "quality": quality,
        })

    def get_recent_transcripts(self, limit: int = 50) -> list[dict]:
        """Return recent transcript events for catch-up replay."""
        return self._transcript_history[-limit:]

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
