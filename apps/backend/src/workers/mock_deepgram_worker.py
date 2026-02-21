"""Mock Deepgram worker for development without a Deepgram API key.

Matches the DeepgramWorker interface (result_queue, start, send_audio, stop)
but emits synthetic TranscriptResult objects instead of connecting to Deepgram.

Accumulates audio for ~2 seconds then emits a mock transcript, alternating
between tab (interviewer) and mic (candidate) speakers.
"""

import asyncio
import random

from src.utils.logging import get_logger
from src.workers.deepgram_worker import TranscriptResult, TranscriptWord, UtteranceEnd

logger = get_logger("mock_deepgram_worker")

# Mock utterances — alternating interviewer / candidate fragments
_INTERVIEWER_UTTERANCES = [
    "Can you walk me through your approach to solving this problem?",
    "What's the time complexity of your solution?",
    "How would you handle edge cases here?",
    "Could you explain why you chose that data structure?",
    "Let's move on to the next question.",
    "Can you optimize this further?",
    "What about space complexity?",
    "How would you test this solution?",
    "Tell me about a challenging project you've worked on.",
    "What tradeoffs are you considering here?",
]

_CANDIDATE_UTTERANCES = [
    "I would start by using a hash map to store the values we've seen so far.",
    "The time complexity would be O of n since we iterate through the array once.",
    "For edge cases I would check if the input array is empty or has only one element.",
    "I chose a hash map because it gives us O of one lookup time on average.",
    "We could use a two pointer approach after sorting the array first.",
    "Let me think about this for a moment. I think we can use dynamic programming here.",
    "The space complexity is O of n because we store at most n elements in the hash map.",
    "I would write unit tests for the basic case, edge cases, and some stress tests.",
    "I recently worked on a distributed system that handled real-time data processing.",
    "The main tradeoff is between time and space complexity here.",
]

# Minimum audio bytes to accumulate before emitting a transcript (~2 seconds at 16kHz 16-bit mono)
_EMIT_THRESHOLD_BYTES = 16000 * 2 * 2  # 2 seconds * 16kHz * 16-bit


class MockDeepgramWorker:
    """Mock replacement for DeepgramWorker — emits synthetic transcripts."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._result_queue: asyncio.Queue[tuple[str, TranscriptResult | UtteranceEnd]] = asyncio.Queue()
        self._running = False
        self._audio_accumulators: dict[str, int] = {}  # channel -> accumulated bytes
        self._utterance_index: dict[str, int] = {"tab": 0, "mic": 0}
        self._time_offset: float = 0.0

    @property
    def result_queue(self) -> "asyncio.Queue[tuple[str, TranscriptResult | UtteranceEnd]]":
        return self._result_queue

    async def start(self, channels: list[str] | None = None) -> None:
        if channels is None:
            channels = ["tab", "mic"]
        for channel in channels:
            self._audio_accumulators[channel] = 0
        self._running = True
        logger.info(
            "mock_deepgram_started",
            session_id=self.session_id,
            channels=channels,
        )

    async def send_audio(self, channel: str, pcm_data: bytes) -> None:
        if not self._running:
            return

        self._audio_accumulators.setdefault(channel, 0)
        self._audio_accumulators[channel] += len(pcm_data)

        if self._audio_accumulators[channel] >= _EMIT_THRESHOLD_BYTES:
            self._audio_accumulators[channel] = 0
            await self._emit_transcript(channel)

    async def stop(self) -> None:
        self._running = False
        logger.info("mock_deepgram_stopped", session_id=self.session_id)

    async def _emit_transcript(self, channel: str) -> None:
        """Emit a synthetic transcript result for the given channel."""
        if channel == "tab":
            utterances = _CANDIDATE_UTTERANCES
            speaker_id = 0
        else:
            utterances = _INTERVIEWER_UTTERANCES
            speaker_id = 1

        idx = self._utterance_index.get(channel, 0)
        text = utterances[idx % len(utterances)]
        self._utterance_index[channel] = idx + 1

        # Build word timestamps
        words_list = text.split()
        word_duration = 0.3
        words = []
        start = self._time_offset
        for w in words_list:
            words.append(TranscriptWord(
                word=w,
                start=start,
                end=start + word_duration,
                confidence=random.uniform(0.90, 0.99),
                speaker=speaker_id,
            ))
            start += word_duration

        duration = len(words_list) * word_duration
        result = TranscriptResult(
            channel_index=0,
            is_final=True,
            speech_final=True,
            text=text,
            confidence=random.uniform(0.92, 0.99),
            words=words,
            speaker=speaker_id,
            start=self._time_offset,
            end=self._time_offset + duration,
            duration=duration,
        )

        self._time_offset += duration + 0.5  # gap between utterances

        await self._result_queue.put((channel, result))

        # Emit utterance end marker
        await self._result_queue.put((
            channel,
            UtteranceEnd(channel_index=0, last_word_end=result.end),
        ))
