"""Transcription service.

Orchestrates the STT pipeline:
  Extension audio → Deepgram worker → Speaker ID → TranscriptEvent
  → Dashboard push + DB persistence

Handles both live and buffered (retroactive) transcription.
"""

import asyncio
import struct
import uuid
from datetime import UTC, datetime
from typing import Any

from src.schemas.transcript import SpeakerRole, TranscriptEventSchema
from src.services.speaker_service import SpeakerService
from src.workers.deepgram_worker import (
    DeepgramWorker,
    TranscriptResult,
    UtteranceEnd,
)
from src.utils.logging import get_logger

logger = get_logger("transcription_service")

# Audio frame header layout (matches extension ws-client.ts)
AUDIO_HEADER_SIZE = 12


def parse_audio_frame(data: bytes) -> dict:
    """Parse a binary audio frame from the extension.

    Header layout (12 bytes):
      [0]     uint8   channel: 0=tab, 1=mic
      [1-8]   float64 timestamp (big-endian, epoch ms)
      [9]     uint8   sampleRate high byte
      [10]    uint8   sampleRate low byte
      [11]    uint8   flags: bit0 = buffered
    """
    if len(data) < AUDIO_HEADER_SIZE:
        raise ValueError(f"Audio frame too small: {len(data)} bytes, need {AUDIO_HEADER_SIZE}")

    channel_byte = data[0]
    timestamp = struct.unpack(">d", data[1:9])[0]
    sample_rate = (data[9] << 8) | data[10]
    flags = data[11]

    return {
        "channel": "tab" if channel_byte == 0 else "mic",
        "timestamp": timestamp,
        "sample_rate": sample_rate,
        "buffered": bool(flags & 0x01),
        "pcm_data": data[AUDIO_HEADER_SIZE:],
    }


class TranscriptionService:
    """Manages real-time transcription for a session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.speaker_service = SpeakerService(session_id)
        self._deepgram_worker: DeepgramWorker | None = None
        self._processor_task: asyncio.Task | None = None
        self._event_callbacks: list[Any] = []
        self._running = False
        self._transcript_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def on_event(self, callback: Any) -> None:
        """Register a callback for transcript events.

        Callback signature: async (event: TranscriptEventSchema) -> None
        """
        self._event_callbacks.append(callback)

    async def start(self) -> None:
        """Start the Deepgram worker and transcript processing."""
        if self._running:
            return

        self._deepgram_worker = DeepgramWorker(self.session_id)
        await self._deepgram_worker.start(channels=["tab", "mic"])
        self._running = True
        self._processor_task = asyncio.create_task(self._process_results())

        logger.info("transcription_started", session_id=self.session_id)

    async def stop(self) -> None:
        """Stop the Deepgram worker and processing."""
        self._running = False

        if self._processor_task and not self._processor_task.done():
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass

        if self._deepgram_worker:
            await self._deepgram_worker.stop()
            self._deepgram_worker = None

        logger.info(
            "transcription_stopped",
            session_id=self.session_id,
            total_transcripts=self._transcript_count,
        )

    async def process_audio_frame(self, raw_frame: bytes) -> None:
        """Process a binary audio frame from the extension WebSocket.

        Parses the header, routes PCM data to the appropriate Deepgram stream.
        """
        frame = parse_audio_frame(raw_frame)

        if self._deepgram_worker is None:
            return

        if frame["buffered"]:
            # Buffered frames are from offline mode — mark for retroactive processing
            logger.debug(
                "buffered_audio_received",
                channel=frame["channel"],
                timestamp=frame["timestamp"],
            )

        await self._deepgram_worker.send_audio(
            channel=frame["channel"],
            pcm_data=frame["pcm_data"],
        )

    async def _process_results(self) -> None:
        """Process transcript results from the Deepgram worker queue."""
        if self._deepgram_worker is None:
            return

        try:
            while self._running:
                try:
                    channel, result = await asyncio.wait_for(
                        self._deepgram_worker.result_queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                if isinstance(result, UtteranceEnd):
                    # Utterance boundary — could trigger analysis
                    continue

                if isinstance(result, TranscriptResult):
                    event = self._result_to_event(channel, result)
                    self._transcript_count += 1

                    # Dispatch to registered callbacks
                    for callback in self._event_callbacks:
                        try:
                            await callback(event)
                        except Exception as e:
                            logger.error(
                                "event_callback_error",
                                error=str(e),
                            )

        except asyncio.CancelledError:
            pass

    def _result_to_event(
        self,
        channel: str,
        result: TranscriptResult,
    ) -> TranscriptEventSchema:
        """Convert a Deepgram TranscriptResult into a TranscriptEventSchema."""
        # Identify the speaker
        speaker_role = self.speaker_service.identify(
            channel=channel,
            deepgram_speaker=result.speaker,
        )

        # Build word timestamps
        word_timestamps = None
        if result.words:
            word_timestamps = [
                {
                    "word": w.word,
                    "start": w.start,
                    "end": w.end,
                    "confidence": w.confidence,
                }
                for w in result.words
            ]

        return TranscriptEventSchema(
            id=str(uuid.uuid4()),
            session_id=self.session_id,
            speaker_role=speaker_role,
            text=result.text,
            is_final=result.is_final,
            confidence=result.confidence,
            timestamp=datetime.now(UTC).isoformat(),
            word_timestamps=word_timestamps,
            is_retroactive=False,
            channel=channel,
        )

    # ── Speaker Calibration ──────────────────────────────────────────────

    def calibrate_speakers(
        self,
        interviewer_speaker_id: int,
        candidate_speaker_id: int | None = None,
    ) -> None:
        """Calibrate speaker mapping from the dashboard."""
        self.speaker_service.calibrate(
            interviewer_speaker_id=interviewer_speaker_id,
            candidate_speaker_id=candidate_speaker_id,
        )

    def get_detected_speakers(self) -> list[dict]:
        """Get detected speakers for the calibration UI."""
        return self.speaker_service.get_detected_speakers()

    # ── State Serialization ──────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize state for snapshots."""
        return {
            "session_id": self.session_id,
            "transcript_count": self._transcript_count,
            "speaker_service": self.speaker_service.to_dict(),
        }
