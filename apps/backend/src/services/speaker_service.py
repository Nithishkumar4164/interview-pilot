"""Speaker identification service.

Three-layer speaker ID system:
  Layer 1: Channel-based (mic = interviewer, tab = remote/candidate)
  Layer 2: Deepgram diarization (speaker IDs within the tab stream)
  Layer 3: Calibration — interviewer speaks a prompt in the first 60s
           to establish voice signature for future disambiguation
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.schemas.transcript import SpeakerRole
from src.utils.logging import get_logger

logger = get_logger("speaker_service")


@dataclass
class SpeakerCalibration:
    """Calibration data gathered in the first 60 seconds of an interview."""

    interviewer_deepgram_speaker: int | None = None
    candidate_deepgram_speaker: int | None = None
    calibrated: bool = False
    calibrated_at: str | None = None
    confidence: float = 0.0


@dataclass
class SpeakerProfile:
    """Tracked speaker profile within a session."""

    role: SpeakerRole
    deepgram_speaker_id: int | None = None
    utterance_count: int = 0
    total_duration_ms: float = 0.0


class SpeakerService:
    """Identifies speakers across three layers of identification.

    Usage:
        service = SpeakerService(session_id)
        role = service.identify(channel="mic", deepgram_speaker=None)  # → interviewer
        role = service.identify(channel="tab", deepgram_speaker=0)      # → candidate (if calibrated)
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.calibration = SpeakerCalibration()
        self._profiles: dict[str, SpeakerProfile] = {}
        self._diarization_map: dict[int, SpeakerRole] = {}
        # Track speaker stats for calibration heuristic
        self._speaker_utterance_counts: dict[int, int] = {}
        self._calibration_window_s = 60.0
        self._session_start: float | None = None

    def identify(
        self,
        channel: str,
        deepgram_speaker: int | None = None,
        timestamp: float | None = None,
    ) -> SpeakerRole:
        """Identify the speaker role using the 3-layer system.

        Args:
            channel: "mic" or "tab"
            deepgram_speaker: Deepgram's diarization speaker ID (0, 1, ...)
            timestamp: Epoch seconds of the utterance

        Returns:
            The identified SpeakerRole.
        """
        # Layer 1: Channel-based identification
        if channel == "mic":
            return SpeakerRole.INTERVIEWER

        # From here, we know channel == "tab" (remote audio)

        # Layer 3: If calibrated, use the diarization map
        if self.calibration.calibrated and deepgram_speaker is not None:
            mapped_role = self._diarization_map.get(deepgram_speaker)
            if mapped_role is not None:
                return mapped_role

        # Layer 2: Deepgram diarization heuristic
        # On the tab channel, the dominant speaker is likely the candidate
        if deepgram_speaker is not None:
            self._track_speaker(deepgram_speaker)
            return self._heuristic_role(deepgram_speaker)

        # Fallback: tab channel without diarization → assume candidate
        return SpeakerRole.CANDIDATE

    def calibrate(
        self,
        interviewer_speaker_id: int,
        candidate_speaker_id: int | None = None,
    ) -> None:
        """Manually calibrate speaker mapping from the dashboard.

        Called after the interviewer speaks a calibration prompt in the
        first 60 seconds. The dashboard shows detected speakers and the
        interviewer selects which one is them.

        Args:
            interviewer_speaker_id: Deepgram speaker ID for the interviewer
            candidate_speaker_id: Deepgram speaker ID for the candidate (optional)
        """
        self._diarization_map[interviewer_speaker_id] = SpeakerRole.INTERVIEWER
        self.calibration.interviewer_deepgram_speaker = interviewer_speaker_id

        if candidate_speaker_id is not None:
            self._diarization_map[candidate_speaker_id] = SpeakerRole.CANDIDATE
            self.calibration.candidate_deepgram_speaker = candidate_speaker_id
        else:
            # Map all other known speakers to candidate
            for spk_id in self._speaker_utterance_counts:
                if spk_id != interviewer_speaker_id and spk_id not in self._diarization_map:
                    self._diarization_map[spk_id] = SpeakerRole.CANDIDATE
                    if candidate_speaker_id is None:
                        self.calibration.candidate_deepgram_speaker = spk_id

        self.calibration.calibrated = True
        self.calibration.calibrated_at = datetime.now(UTC).isoformat()
        self.calibration.confidence = 0.9

        logger.info(
            "speaker_calibrated",
            session_id=self.session_id,
            interviewer_speaker=interviewer_speaker_id,
            candidate_speaker=self.calibration.candidate_deepgram_speaker,
        )

    def auto_calibrate(self) -> bool:
        """Attempt automatic calibration based on speaking patterns.

        Heuristic: On the tab stream, the person who speaks most in
        the first 60s during the tab channel is likely the candidate
        (since the interviewer's voice is also on the mic channel).

        Returns True if auto-calibration was successful.
        """
        if self.calibration.calibrated:
            return True

        if len(self._speaker_utterance_counts) < 2:
            return False

        # Find the two most frequent speakers
        sorted_speakers = sorted(
            self._speaker_utterance_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        # The speaker with the most utterances on the tab channel
        # is likely the candidate (since interview is mostly Q&A)
        candidate_id = sorted_speakers[0][0]
        interviewer_id = sorted_speakers[1][0]

        self.calibrate(interviewer_speaker_id=interviewer_id, candidate_speaker_id=candidate_id)
        self.calibration.confidence = 0.6  # Lower confidence for auto-calibration
        return True

    def get_detected_speakers(self) -> list[dict]:
        """Return detected speakers for the dashboard calibration UI."""
        return [
            {
                "deepgram_speaker_id": spk_id,
                "utterance_count": count,
                "role": self._diarization_map.get(spk_id, SpeakerRole.UNKNOWN).value,
            }
            for spk_id, count in sorted(
                self._speaker_utterance_counts.items(),
                key=lambda x: x[1],
                reverse=True,
            )
        ]

    def _track_speaker(self, speaker_id: int) -> None:
        """Track a speaker's utterance for calibration statistics."""
        self._speaker_utterance_counts[speaker_id] = (
            self._speaker_utterance_counts.get(speaker_id, 0) + 1
        )

    def _heuristic_role(self, speaker_id: int) -> SpeakerRole:
        """Use heuristic to guess role before calibration.

        Before calibration, on the tab channel:
        - If only 1 speaker detected, it's the candidate
        - If 2+ speakers, the one with more utterances is likely the candidate
        """
        if len(self._speaker_utterance_counts) <= 1:
            return SpeakerRole.CANDIDATE

        # Find the speaker with most utterances
        max_speaker = max(
            self._speaker_utterance_counts,
            key=self._speaker_utterance_counts.get,  # type: ignore
        )

        if speaker_id == max_speaker:
            return SpeakerRole.CANDIDATE
        else:
            # Could be interviewer echo on tab, or a second interviewer
            return SpeakerRole.UNKNOWN

    def to_dict(self) -> dict:
        """Serialize state for session snapshots."""
        return {
            "calibration": {
                "interviewer_deepgram_speaker": self.calibration.interviewer_deepgram_speaker,
                "candidate_deepgram_speaker": self.calibration.candidate_deepgram_speaker,
                "calibrated": self.calibration.calibrated,
                "calibrated_at": self.calibration.calibrated_at,
                "confidence": self.calibration.confidence,
            },
            "diarization_map": {
                str(k): v.value for k, v in self._diarization_map.items()
            },
            "speaker_counts": dict(self._speaker_utterance_counts),
        }

    @classmethod
    def from_dict(cls, session_id: str, data: dict) -> "SpeakerService":
        """Restore from a session snapshot."""
        service = cls(session_id)
        cal = data.get("calibration", {})
        service.calibration = SpeakerCalibration(
            interviewer_deepgram_speaker=cal.get("interviewer_deepgram_speaker"),
            candidate_deepgram_speaker=cal.get("candidate_deepgram_speaker"),
            calibrated=cal.get("calibrated", False),
            calibrated_at=cal.get("calibrated_at"),
            confidence=cal.get("confidence", 0.0),
        )
        for spk_str, role_str in data.get("diarization_map", {}).items():
            service._diarization_map[int(spk_str)] = SpeakerRole(role_str)
        service._speaker_utterance_counts = {
            int(k): v for k, v in data.get("speaker_counts", {}).items()
        }
        return service
