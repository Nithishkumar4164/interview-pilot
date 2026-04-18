"""Deepgram Nova-2 WebSocket STT worker.

Manages WebSocket connections to the Deepgram real-time transcription API.
Supports two parallel streams (tab and mic channels) and handles
both live and buffered audio transcription.
"""

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from enum import Enum

import websockets
from websockets.asyncio.client import ClientConnection

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("deepgram_worker")

DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"

# Default Deepgram connection parameters
DEEPGRAM_PARAMS = {
    "model": "nova-2",
    "language": "en",
    "smart_format": "true",
    "interim_results": "true",
    "diarize": "true",
    "punctuate": "true",
    "utterance_end_ms": "1000",
    "vad_events": "true",
    "encoding": "linear16",
    "sample_rate": "16000",
    "channels": "1",
}


class StreamState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"


@dataclass
class TranscriptWord:
    """A single word with timing information from Deepgram."""

    word: str
    start: float
    end: float
    confidence: float
    speaker: int | None = None


@dataclass
class TranscriptResult:
    """A parsed transcript result from Deepgram."""

    channel_index: int
    is_final: bool
    speech_final: bool
    text: str
    confidence: float
    words: list[TranscriptWord] = field(default_factory=list)
    speaker: int | None = None
    start: float = 0.0
    end: float = 0.0
    duration: float = 0.0


@dataclass
class UtteranceEnd:
    """Marker for an utterance boundary detected by VAD."""

    channel_index: int
    last_word_end: float


class DeepgramStream:
    """Manages a single WebSocket connection to the Deepgram STT API."""

    def __init__(
        self,
        channel: str,
        session_id: str,
        on_transcript: "asyncio.Queue[TranscriptResult | UtteranceEnd] | None" = None,
        api_key: str | None = None,
        params: dict[str, str] | None = None,
    ) -> None:
        self.channel = channel
        self.session_id = session_id
        self.api_key = api_key or settings.deepgram_api_key
        self.params = {**DEEPGRAM_PARAMS, **(params or {})}
        self.state = StreamState.DISCONNECTED
        self._ws: ClientConnection | None = None
        self._recv_task: asyncio.Task | None = None
        self._transcript_queue = on_transcript or asyncio.Queue()

    @property
    def transcript_queue(self) -> "asyncio.Queue[TranscriptResult | UtteranceEnd]":
        return self._transcript_queue

    def _build_url(self) -> str:
        query = "&".join(f"{k}={v}" for k, v in self.params.items())
        return f"{DEEPGRAM_WS_URL}?{query}"

    async def connect(self) -> None:
        """Open a WebSocket connection to Deepgram."""
        if self.state in (StreamState.CONNECTED, StreamState.CONNECTING):
            return

        self.state = StreamState.CONNECTING
        url = self._build_url()

        try:
            self._ws = await websockets.connect(
                url,
                additional_headers={"Authorization": f"Token {self.api_key}"},
                ping_interval=5,
                ping_timeout=20,
                close_timeout=10,
            )
            self.state = StreamState.CONNECTED
            self._recv_task = asyncio.create_task(self._receive_loop())

            logger.info(
                "deepgram_connected",
                channel=self.channel,
                session_id=self.session_id,
            )
        except Exception as e:
            self.state = StreamState.DISCONNECTED
            logger.error(
                "deepgram_connect_failed",
                channel=self.channel,
                session_id=self.session_id,
                error=str(e),
            )
            raise

    async def send_audio(self, pcm_data: bytes) -> None:
        """Send raw PCM audio data to Deepgram."""
        if self._ws is None or self.state != StreamState.CONNECTED:
            return
        try:
            await self._ws.send(pcm_data)
        except Exception as e:
            logger.error(
                "deepgram_send_error",
                channel=self.channel,
                error=str(e),
            )
            self.state = StreamState.DISCONNECTED

    async def close(self) -> None:
        """Gracefully close the Deepgram connection."""
        if self._ws is None:
            return

        self.state = StreamState.CLOSING

        try:
            # Send close stream message per Deepgram protocol
            await self._ws.send(json.dumps({"type": "CloseStream"}))
            # Deepgram doesn't publish a strict deadline; 500ms is long
            # enough to receive trailing finals without noticeably delaying
            # session teardown.
            await asyncio.sleep(0.5)
        except Exception:
            logger.warning("deepgram_close_send_failed", exc_info=True)

        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recv_task

        try:
            await self._ws.close()
        except Exception:
            logger.warning("deepgram_ws_close_failed", exc_info=True)

        self._ws = None
        self.state = StreamState.DISCONNECTED

        logger.info(
            "deepgram_disconnected",
            channel=self.channel,
            session_id=self.session_id,
        )

    async def keep_alive(self) -> None:
        """Send a keep-alive message to prevent timeout."""
        if self._ws and self.state == StreamState.CONNECTED:
            try:
                await self._ws.send(json.dumps({"type": "KeepAlive"}))
            except Exception:
                logger.warning("deepgram_keepalive_failed", exc_info=True)

    async def _receive_loop(self) -> None:
        """Continuously receive and parse transcript results from Deepgram."""
        if self._ws is None:
            return

        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    continue  # Skip binary messages

                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type")

                if msg_type == "Results":
                    result = self._parse_result(data)
                    if result and result.text.strip():
                        await self._transcript_queue.put(result)

                elif msg_type == "UtteranceEnd":
                    end = UtteranceEnd(
                        channel_index=0,
                        last_word_end=data.get("last_word_end", 0.0),
                    )
                    await self._transcript_queue.put(end)

                elif msg_type == "Metadata":
                    logger.debug(
                        "deepgram_metadata",
                        channel=self.channel,
                        request_id=data.get("request_id"),
                    )

                elif msg_type == "Error":
                    logger.error(
                        "deepgram_error",
                        channel=self.channel,
                        error=data.get("message", "Unknown error"),
                    )

        except websockets.exceptions.ConnectionClosed:
            logger.warning(
                "deepgram_connection_closed",
                channel=self.channel,
                session_id=self.session_id,
            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(
                "deepgram_recv_error",
                channel=self.channel,
                error=str(e),
            )
        finally:
            self.state = StreamState.DISCONNECTED

    def _parse_result(self, data: dict) -> TranscriptResult | None:
        """Parse a Deepgram Results message into a TranscriptResult."""
        channel_data = data.get("channel", {})
        alternatives = channel_data.get("alternatives", [])

        if not alternatives:
            return None

        best = alternatives[0]
        text = best.get("transcript", "")

        if not text:
            return None

        words = []
        speaker = None
        for w in best.get("words", []):
            tw = TranscriptWord(
                word=w.get("word", ""),
                start=w.get("start", 0.0),
                end=w.get("end", 0.0),
                confidence=w.get("confidence", 0.0),
                speaker=w.get("speaker"),
            )
            words.append(tw)
            # Use the most common speaker across words
            if tw.speaker is not None:
                speaker = tw.speaker

        return TranscriptResult(
            channel_index=data.get("channel_index", [0])[0] if isinstance(data.get("channel_index"), list) else 0,
            is_final=data.get("is_final", False),
            speech_final=data.get("speech_final", False),
            text=text,
            confidence=best.get("confidence", 0.0),
            words=words,
            speaker=speaker,
            start=data.get("start", 0.0),
            end=data.get("start", 0.0) + data.get("duration", 0.0),
            duration=data.get("duration", 0.0),
        )


class DeepgramWorker:
    """Manages parallel Deepgram streams for a session (tab + mic)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._result_queue: asyncio.Queue[tuple[str, TranscriptResult | UtteranceEnd]] = asyncio.Queue()
        self._streams: dict[str, DeepgramStream] = {}

    @property
    def result_queue(self) -> "asyncio.Queue[tuple[str, TranscriptResult | UtteranceEnd]]":
        """Queue of (channel, result) tuples."""
        return self._result_queue

    async def start(self, channels: list[str] | None = None) -> None:
        """Start Deepgram streams for the given channels."""
        if channels is None:
            channels = ["tab", "mic"]

        for channel in channels:
            stream = DeepgramStream(
                channel=channel,
                session_id=self.session_id,
            )
            self._streams[channel] = stream
            await stream.connect()
            # Start a forwarder task that tags results with channel
            asyncio.create_task(self._forward_results(channel, stream))

        logger.info(
            "deepgram_worker_started",
            session_id=self.session_id,
            channels=channels,
        )

    async def send_audio(self, channel: str, pcm_data: bytes) -> None:
        """Send audio data to the appropriate Deepgram stream.

        Auto-reconnects if the stream disconnected (e.g. Deepgram closed
        an idle connection before mic capture started).
        """
        stream = self._streams.get(channel)
        if not stream:
            return

        if stream.state == StreamState.DISCONNECTED:
            logger.info(
                "deepgram_stream_reconnecting",
                channel=channel,
                session_id=self.session_id,
            )
            try:
                await stream.connect()
                asyncio.create_task(self._forward_results(channel, stream))
            except Exception as e:
                logger.error(
                    "deepgram_reconnect_failed",
                    channel=channel,
                    session_id=self.session_id,
                    error=str(e),
                )
                return

        await stream.send_audio(pcm_data)

    async def stop(self) -> None:
        """Close all Deepgram streams."""
        for stream in self._streams.values():
            await stream.close()
        self._streams.clear()

        logger.info(
            "deepgram_worker_stopped",
            session_id=self.session_id,
        )

    async def _forward_results(self, channel: str, stream: DeepgramStream) -> None:
        """Forward results from a per-channel queue to the unified result queue."""
        try:
            while stream.state != StreamState.DISCONNECTED or not stream.transcript_queue.empty():
                try:
                    result = await asyncio.wait_for(stream.transcript_queue.get(), timeout=1.0)
                    await self._result_queue.put((channel, result))
                except TimeoutError:
                    if stream.state == StreamState.DISCONNECTED:
                        break
        except asyncio.CancelledError:
            pass
