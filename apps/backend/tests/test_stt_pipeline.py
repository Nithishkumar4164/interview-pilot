"""Tests for M4 — STT Pipeline.

Covers:
- Audio frame parsing (binary protocol)
- Speaker identification (3-layer system)
- Transcription service (event conversion)
- Deepgram worker (result parsing)
- WebSocket manager (connection tracking)
"""

import struct
import uuid
from unittest.mock import AsyncMock

import pytest

from src.schemas.transcript import SpeakerRole
from src.services.speaker_service import SpeakerService
from src.services.transcription_service import (
    AUDIO_HEADER_SIZE,
    TranscriptionService,
    parse_audio_frame,
)
from src.websocket.manager import ConnectionManager
from src.workers.deepgram_worker import (
    DeepgramStream,
    DeepgramWorker,
    StreamState,
    TranscriptResult,
    TranscriptWord,
)

# ── Audio Frame Parsing ──────────────────────────────────────────────


class TestAudioFrameParsing:
    """Test the 12-byte binary audio frame protocol."""

    def _build_frame(
        self,
        channel: int = 0,
        timestamp: float = 1700000000.0,
        sample_rate: int = 16000,
        flags: int = 0,
        pcm: bytes = b"\x00\x01\x02\x03",
    ) -> bytes:
        header = struct.pack(">B", channel)
        header += struct.pack(">d", timestamp)
        header += struct.pack(">BB", sample_rate >> 8, sample_rate & 0xFF)
        header += struct.pack(">B", flags)
        return header + pcm

    def test_parse_tab_channel(self):
        frame = self._build_frame(channel=0, timestamp=1700000000.0)
        result = parse_audio_frame(frame)
        assert result["channel"] == "tab"
        assert result["timestamp"] == pytest.approx(1700000000.0)
        assert result["sample_rate"] == 16000
        assert result["buffered"] is False
        assert result["pcm_data"] == b"\x00\x01\x02\x03"

    def test_parse_mic_channel(self):
        frame = self._build_frame(channel=1)
        result = parse_audio_frame(frame)
        assert result["channel"] == "mic"

    def test_parse_buffered_flag(self):
        frame = self._build_frame(flags=0x01)
        result = parse_audio_frame(frame)
        assert result["buffered"] is True

    def test_parse_custom_sample_rate(self):
        frame = self._build_frame(sample_rate=48000)
        result = parse_audio_frame(frame)
        assert result["sample_rate"] == 48000

    def test_parse_empty_pcm(self):
        frame = self._build_frame(pcm=b"")
        result = parse_audio_frame(frame)
        assert result["pcm_data"] == b""

    def test_parse_large_pcm(self):
        pcm = b"\xab" * 8000  # 250ms at 16kHz 16-bit
        frame = self._build_frame(pcm=pcm)
        result = parse_audio_frame(frame)
        assert len(result["pcm_data"]) == 8000

    def test_parse_too_small_raises(self):
        with pytest.raises(ValueError, match="too small"):
            parse_audio_frame(b"\x00" * 5)

    def test_header_size_constant(self):
        assert AUDIO_HEADER_SIZE == 12


# ── Speaker Service ──────────────────────────────────────────────────


class TestSpeakerService:
    """Test the 3-layer speaker identification system."""

    def setup_method(self):
        self.service = SpeakerService("test-session")

    # Layer 1: Channel-based

    def test_mic_always_interviewer(self):
        role = self.service.identify(channel="mic", deepgram_speaker=None)
        assert role == SpeakerRole.INTERVIEWER

    def test_mic_ignores_diarization(self):
        role = self.service.identify(channel="mic", deepgram_speaker=5)
        assert role == SpeakerRole.INTERVIEWER

    # Layer 2: Heuristic (before calibration)

    def test_tab_single_speaker_is_candidate(self):
        role = self.service.identify(channel="tab", deepgram_speaker=0)
        assert role == SpeakerRole.CANDIDATE

    def test_tab_dominant_speaker_is_candidate(self):
        # Speaker 0 speaks 5 times
        for _ in range(5):
            self.service.identify(channel="tab", deepgram_speaker=0)
        # Speaker 1 speaks 2 times
        for _ in range(2):
            self.service.identify(channel="tab", deepgram_speaker=1)

        # Speaker 0 (dominant) should be candidate
        assert self.service.identify(channel="tab", deepgram_speaker=0) == SpeakerRole.CANDIDATE
        # Speaker 1 (less frequent) should be unknown
        assert self.service.identify(channel="tab", deepgram_speaker=1) == SpeakerRole.UNKNOWN

    def test_tab_no_diarization_defaults_candidate(self):
        role = self.service.identify(channel="tab", deepgram_speaker=None)
        assert role == SpeakerRole.CANDIDATE

    # Layer 3: Calibration

    def test_calibration_maps_speakers(self):
        # Build some history
        for _ in range(3):
            self.service.identify(channel="tab", deepgram_speaker=0)
            self.service.identify(channel="tab", deepgram_speaker=1)

        # Calibrate: speaker 0 is interviewer
        self.service.calibrate(interviewer_speaker_id=0, candidate_speaker_id=1)

        assert self.service.calibration.calibrated is True
        assert self.service.identify(channel="tab", deepgram_speaker=0) == SpeakerRole.INTERVIEWER
        assert self.service.identify(channel="tab", deepgram_speaker=1) == SpeakerRole.CANDIDATE

    def test_calibration_auto_assigns_candidate(self):
        """When no explicit candidate_speaker_id, other speakers become candidate."""
        for _ in range(3):
            self.service.identify(channel="tab", deepgram_speaker=0)
            self.service.identify(channel="tab", deepgram_speaker=1)

        self.service.calibrate(interviewer_speaker_id=0)  # No candidate specified

        assert self.service.identify(channel="tab", deepgram_speaker=0) == SpeakerRole.INTERVIEWER
        assert self.service.identify(channel="tab", deepgram_speaker=1) == SpeakerRole.CANDIDATE

    def test_auto_calibration(self):
        # Need at least 2 speakers
        for _ in range(10):
            self.service.identify(channel="tab", deepgram_speaker=0)
        for _ in range(3):
            self.service.identify(channel="tab", deepgram_speaker=1)

        result = self.service.auto_calibrate()
        assert result is True
        assert self.service.calibration.calibrated is True
        assert self.service.calibration.confidence == 0.6  # Lower for auto

    def test_auto_calibration_fails_single_speaker(self):
        for _ in range(5):
            self.service.identify(channel="tab", deepgram_speaker=0)

        result = self.service.auto_calibrate()
        assert result is False
        assert self.service.calibration.calibrated is False

    def test_get_detected_speakers(self):
        for _ in range(5):
            self.service.identify(channel="tab", deepgram_speaker=0)
        for _ in range(2):
            self.service.identify(channel="tab", deepgram_speaker=1)

        speakers = self.service.get_detected_speakers()
        assert len(speakers) == 2
        assert speakers[0]["deepgram_speaker_id"] == 0
        assert speakers[0]["utterance_count"] == 5
        assert speakers[1]["deepgram_speaker_id"] == 1
        assert speakers[1]["utterance_count"] == 2

    # Serialization

    def test_to_dict_and_from_dict(self):
        for _ in range(3):
            self.service.identify(channel="tab", deepgram_speaker=0)
        self.service.calibrate(interviewer_speaker_id=0, candidate_speaker_id=1)

        data = self.service.to_dict()
        restored = SpeakerService.from_dict("test-session", data)

        assert restored.calibration.calibrated is True
        assert restored.calibration.interviewer_deepgram_speaker == 0
        assert restored.identify(channel="tab", deepgram_speaker=0) == SpeakerRole.INTERVIEWER


# ── Transcription Service ────────────────────────────────────────────


class TestTranscriptionService:
    """Test transcript event conversion and callback dispatch."""

    def setup_method(self):
        self.service = TranscriptionService("test-session")

    def test_result_to_event_basic(self):
        result = TranscriptResult(
            channel_index=0,
            is_final=True,
            speech_final=True,
            text="Hello, how are you?",
            confidence=0.95,
            words=[
                TranscriptWord(word="Hello", start=0.0, end=0.3, confidence=0.98),
                TranscriptWord(word="how", start=0.35, end=0.5, confidence=0.96),
                TranscriptWord(word="are", start=0.52, end=0.6, confidence=0.97),
                TranscriptWord(word="you", start=0.62, end=0.8, confidence=0.95),
            ],
            speaker=0,
        )
        event = self.service._result_to_event("tab", result)

        assert event.session_id == "test-session"
        assert event.speaker_role == SpeakerRole.CANDIDATE  # tab + single speaker
        assert event.text == "Hello, how are you?"
        assert event.is_final is True
        assert event.confidence == 0.95
        assert event.channel == "tab"
        assert event.is_retroactive is False
        assert len(event.word_timestamps) == 4
        assert event.word_timestamps[0].word == "Hello"

    def test_result_to_event_mic_channel(self):
        result = TranscriptResult(
            channel_index=0,
            is_final=True,
            speech_final=True,
            text="Can you explain that?",
            confidence=0.9,
        )
        event = self.service._result_to_event("mic", result)
        assert event.speaker_role == SpeakerRole.INTERVIEWER

    def test_result_to_event_no_words(self):
        result = TranscriptResult(
            channel_index=0,
            is_final=False,
            speech_final=False,
            text="partial",
            confidence=0.8,
        )
        event = self.service._result_to_event("tab", result)
        assert event.word_timestamps is None

    def test_event_has_uuid(self):
        result = TranscriptResult(
            channel_index=0,
            is_final=True,
            speech_final=True,
            text="test",
            confidence=0.9,
        )
        event = self.service._result_to_event("tab", result)
        # Should be a valid UUID string
        uuid.UUID(event.id)

    @pytest.mark.asyncio
    async def test_event_callbacks(self):
        events_received = []

        async def callback(event):
            events_received.append(event)

        self.service.on_event(callback)

        # Simulate processing by directly calling the internal method
        result = TranscriptResult(
            channel_index=0,
            is_final=True,
            speech_final=True,
            text="test callback",
            confidence=0.9,
        )
        event = self.service._result_to_event("tab", result)
        for cb in self.service._event_callbacks:
            await cb(event)

        assert len(events_received) == 1
        assert events_received[0].text == "test callback"

    def test_to_dict(self):
        data = self.service.to_dict()
        assert data["session_id"] == "test-session"
        assert data["transcript_count"] == 0
        assert "speaker_service" in data


# ── Deepgram Worker Result Parsing ───────────────────────────────────


class TestDeepgramResultParsing:
    """Test Deepgram result message parsing."""

    def setup_method(self):
        self.stream = DeepgramStream(
            channel="tab",
            session_id="test",
            api_key="test-key",
        )

    def test_parse_basic_result(self):
        data = {
            "type": "Results",
            "channel_index": [0],
            "is_final": True,
            "speech_final": True,
            "start": 1.5,
            "duration": 2.0,
            "channel": {
                "alternatives": [
                    {
                        "transcript": "Hello world",
                        "confidence": 0.95,
                        "words": [
                            {
                                "word": "Hello",
                                "start": 1.5,
                                "end": 1.8,
                                "confidence": 0.98,
                                "speaker": 0,
                            },
                            {
                                "word": "world",
                                "start": 1.9,
                                "end": 2.3,
                                "confidence": 0.93,
                                "speaker": 0,
                            },
                        ],
                    }
                ],
            },
        }

        result = self.stream._parse_result(data)
        assert result is not None
        assert result.text == "Hello world"
        assert result.confidence == 0.95
        assert result.is_final is True
        assert result.speech_final is True
        assert result.speaker == 0
        assert len(result.words) == 2
        assert result.words[0].word == "Hello"

    def test_parse_empty_alternatives(self):
        data = {"channel": {"alternatives": []}}
        assert self.stream._parse_result(data) is None

    def test_parse_empty_transcript(self):
        data = {"channel": {"alternatives": [{"transcript": "", "confidence": 0.0}]}}
        assert self.stream._parse_result(data) is None

    def test_parse_interim_result(self):
        data = {
            "type": "Results",
            "channel_index": [0],
            "is_final": False,
            "speech_final": False,
            "start": 0.0,
            "duration": 0.5,
            "channel": {
                "alternatives": [
                    {
                        "transcript": "hel",
                        "confidence": 0.7,
                        "words": [],
                    }
                ],
            },
        }
        result = self.stream._parse_result(data)
        assert result is not None
        assert result.is_final is False
        assert result.text == "hel"

    def test_stream_initial_state(self):
        assert self.stream.state == StreamState.DISCONNECTED

    def test_worker_initial_state(self):
        worker = DeepgramWorker("test")
        assert len(worker._streams) == 0


# ── WebSocket Connection Manager ─────────────────────────────────────


class TestConnectionManager:
    """Test WebSocket connection management."""

    def setup_method(self):
        self.manager = ConnectionManager()

    @pytest.mark.asyncio
    async def test_connect_and_count(self):
        ws = AsyncMock()
        await self.manager.connect("session-1", ws)
        assert self.manager.get_connection_count("session-1") == 1
        assert self.manager.has_connections("session-1") is True

    @pytest.mark.asyncio
    async def test_multiple_connections(self):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await self.manager.connect("session-1", ws1)
        await self.manager.connect("session-1", ws2)
        assert self.manager.get_connection_count("session-1") == 2

    @pytest.mark.asyncio
    async def test_disconnect(self):
        ws = AsyncMock()
        await self.manager.connect("session-1", ws)
        await self.manager.disconnect("session-1", ws)
        assert self.manager.get_connection_count("session-1") == 0
        assert self.manager.has_connections("session-1") is False

    @pytest.mark.asyncio
    async def test_disconnect_unknown_session(self):
        ws = AsyncMock()
        # Should not raise
        await self.manager.disconnect("nonexistent", ws)

    @pytest.mark.asyncio
    async def test_broadcast(self):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await self.manager.connect("session-1", ws1)
        await self.manager.connect("session-1", ws2)

        await self.manager.broadcast("session-1", {"type": "test", "data": "hello"})

        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_adds_timestamp(self):
        ws = AsyncMock()
        await self.manager.connect("session-1", ws)

        msg = {"type": "test"}
        await self.manager.broadcast("session-1", msg)

        assert "timestamp" in msg

    @pytest.mark.asyncio
    async def test_broadcast_no_connections(self):
        # Should not raise
        await self.manager.broadcast("empty-session", {"type": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_cleans_dead_connections(self):
        ws_alive = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_text.side_effect = Exception("Connection closed")

        await self.manager.connect("session-1", ws_alive)
        await self.manager.connect("session-1", ws_dead)
        assert self.manager.get_connection_count("session-1") == 2

        await self.manager.broadcast("session-1", {"type": "test"})

        # Dead connection should be removed
        assert self.manager.get_connection_count("session-1") == 1

    @pytest.mark.asyncio
    async def test_send_transcript(self):
        ws = AsyncMock()
        await self.manager.connect("session-1", ws)

        await self.manager.send_transcript("session-1", {"text": "hello"})
        ws.send_text.assert_called_once()
        import json

        sent = json.loads(ws.send_text.call_args[0][0])
        assert sent["type"] == "transcript"
        assert sent["session_id"] == "session-1"
        assert sent["payload"]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_send_error(self):
        ws = AsyncMock()
        await self.manager.connect("session-1", ws)

        await self.manager.send_error("session-1", "something broke")
        import json

        sent = json.loads(ws.send_text.call_args[0][0])
        assert sent["type"] == "error"
        assert sent["payload"]["message"] == "something broke"

    @pytest.mark.asyncio
    async def test_separate_sessions(self):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await self.manager.connect("session-1", ws1)
        await self.manager.connect("session-2", ws2)

        await self.manager.broadcast("session-1", {"type": "test"})

        ws1.send_text.assert_called_once()
        ws2.send_text.assert_not_called()
