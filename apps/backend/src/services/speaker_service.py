"""Speaker identification service.

Three-layer speaker ID system:
  Layer 1: Channel-based (mic = interviewer, tab = remote/candidate)
  Layer 2: Deepgram diarization (speaker IDs within the tab stream)
  Layer 3: Calibration — interviewer speaks a prompt in the first 60s
           to establish voice signature for future disambiguation

On top of the three layers, a Root Speaker / Speech Vector management
layer allows the dashboard user to create named speakers and reassign
Deepgram speaker segments at both vector and transcript granularity.
"""

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.schemas.transcript import SpeakerRole
from src.utils.logging import get_logger

logger = get_logger("speaker_service")

# ── Default colours ──────────────────────────────────────────────────

DEFAULT_INTERVIEWER_COLOR = "#818cf8"
DEFAULT_CANDIDATE_COLOR = "#34d399"

# Maximum recent transcript IDs tracked per speech vector
MAX_TRANSCRIPT_IDS = 20


# ── Dataclasses ──────────────────────────────────────────────────────

@dataclass
class RootSpeaker:
    """A user-visible speaker label that groups speech vectors."""

    id: str
    label: str
    color: str
    is_default: bool = False
    speech_vector_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "color": self.color,
            "is_default": self.is_default,
            "speech_vector_ids": list(self.speech_vector_ids),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RootSpeaker":
        return cls(
            id=data["id"],
            label=data["label"],
            color=data["color"],
            is_default=data.get("is_default", False),
            speech_vector_ids=list(data.get("speech_vector_ids", [])),
        )


@dataclass
class SpeechVector:
    """A unique speaker voice identified by Deepgram or created virtually."""

    id: str
    root_speaker_id: str
    deepgram_speaker_id: int | None = None
    channel: str = ""  # 'mic' | 'tab' | 'virtual'
    utterance_count: int = 0
    transcript_ids: list[str] = field(default_factory=list)
    is_virtual: bool = False

    def add_transcript(self, transcript_id: str) -> None:
        self.transcript_ids.append(transcript_id)
        if len(self.transcript_ids) > MAX_TRANSCRIPT_IDS:
            self.transcript_ids = self.transcript_ids[-MAX_TRANSCRIPT_IDS:]
        self.utterance_count += 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "root_speaker_id": self.root_speaker_id,
            "deepgram_speaker_id": self.deepgram_speaker_id,
            "channel": self.channel,
            "utterance_count": self.utterance_count,
            "transcript_ids": list(self.transcript_ids),
            "is_virtual": self.is_virtual,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SpeechVector":
        return cls(
            id=data["id"],
            root_speaker_id=data["root_speaker_id"],
            deepgram_speaker_id=data.get("deepgram_speaker_id"),
            channel=data.get("channel", ""),
            utterance_count=data.get("utterance_count", 0),
            transcript_ids=list(data.get("transcript_ids", [])),
            is_virtual=data.get("is_virtual", False),
        )


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


# ── Service ──────────────────────────────────────────────────────────

class SpeakerService:
    """Identifies speakers across three layers of identification
    and manages Root Speaker / Speech Vector associations.

    Usage:
        service = SpeakerService(session_id)
        role, rs_id, sv_id = service.identify(channel="mic", deepgram_speaker=None)
    """

    # ── Default root speaker IDs (deterministic for tests) ───────────
    DEFAULT_INTERVIEWER_RS = "rs-default-interviewer"
    DEFAULT_CANDIDATE_RS = "rs-default-candidate"

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.calibration = SpeakerCalibration()
        self._profiles: dict[str, SpeakerProfile] = {}
        self._diarization_map: dict[int, SpeakerRole] = {}
        self._speaker_utterance_counts: dict[int, int] = {}
        self._calibration_window_s = 60.0
        self._session_start: float | None = None

        # ── Root Speaker / Speech Vector state ───────────────────────
        self._root_speakers: dict[str, RootSpeaker] = {}
        self._speech_vectors: dict[str, SpeechVector] = {}
        self._sv_to_rs: dict[str, str] = {}
        self._transcript_sv_override: dict[str, str] = {}

        # Seed two default root speakers
        self._seed_defaults()

    # ── Seed helpers ─────────────────────────────────────────────────

    def _seed_defaults(self) -> None:
        interviewer = RootSpeaker(
            id=self.DEFAULT_INTERVIEWER_RS,
            label="Interviewer",
            color=DEFAULT_INTERVIEWER_COLOR,
            is_default=True,
        )
        candidate = RootSpeaker(
            id=self.DEFAULT_CANDIDATE_RS,
            label="Candidate",
            color=DEFAULT_CANDIDATE_COLOR,
            is_default=True,
        )
        self._root_speakers[interviewer.id] = interviewer
        self._root_speakers[candidate.id] = candidate

    # ── Core identification ──────────────────────────────────────────

    def identify(
        self,
        channel: str,
        deepgram_speaker: int | None = None,
        timestamp: float | None = None,
        transcript_id: str | None = None,
    ) -> tuple[SpeakerRole, str, str, bool]:
        """Identify the speaker role using the 3-layer system.

        Returns:
            (speaker_role, root_speaker_id, speech_vector_id, sv_created)
        """
        # Determine the SpeakerRole via the legacy 3-layer logic
        role = self._identify_role(channel, deepgram_speaker, timestamp)

        # Check if the speech vector already exists before ensuring it
        sv_id = self._make_sv_id(channel, deepgram_speaker)
        sv_existed = sv_id in self._speech_vectors

        # Get-or-create the speech vector for this (channel, deepgram_speaker)
        sv = self._ensure_speech_vector(channel, deepgram_speaker)

        # Auto-assign the speech vector to a root speaker if not yet done
        if sv.root_speaker_id == "":
            self._auto_assign_root(sv, channel, deepgram_speaker)

        # Track transcript on the speech vector
        if transcript_id:
            sv.add_transcript(transcript_id)

        return role, sv.root_speaker_id, sv.id, not sv_existed

    def _identify_role(
        self,
        channel: str,
        deepgram_speaker: int | None,
        timestamp: float | None = None,
    ) -> SpeakerRole:
        """Legacy 3-layer speaker role identification."""
        # Layer 1: Channel-based
        if channel == "mic":
            return SpeakerRole.INTERVIEWER

        # Layer 3: Calibrated diarization map
        if self.calibration.calibrated and deepgram_speaker is not None:
            mapped_role = self._diarization_map.get(deepgram_speaker)
            if mapped_role is not None:
                return mapped_role

        # Layer 2: Heuristic
        if deepgram_speaker is not None:
            self._track_speaker(deepgram_speaker)
            return self._heuristic_role(deepgram_speaker)

        return SpeakerRole.CANDIDATE

    # ── Speech vector management ─────────────────────────────────────

    def _make_sv_id(self, channel: str, deepgram_speaker: int | None) -> str:
        dg = deepgram_speaker if deepgram_speaker is not None else 0
        return f"sv-dg-{channel}-{dg}"

    def _ensure_speech_vector(
        self,
        channel: str,
        deepgram_speaker: int | None,
    ) -> SpeechVector:
        sv_id = self._make_sv_id(channel, deepgram_speaker)
        if sv_id in self._speech_vectors:
            return self._speech_vectors[sv_id]

        sv = SpeechVector(
            id=sv_id,
            root_speaker_id="",  # assigned by _auto_assign_root
            deepgram_speaker_id=deepgram_speaker,
            channel=channel,
        )
        self._speech_vectors[sv_id] = sv
        return sv

    def _auto_assign_root(
        self,
        sv: SpeechVector,
        channel: str,
        deepgram_speaker: int | None,
    ) -> None:
        """Assign a speech vector to a root speaker based on channel heuristic."""
        if channel == "mic":
            target_rs = self.DEFAULT_INTERVIEWER_RS
        else:
            target_rs = self.DEFAULT_CANDIDATE_RS

        sv.root_speaker_id = target_rs
        self._sv_to_rs[sv.id] = target_rs
        rs = self._root_speakers[target_rs]
        if sv.id not in rs.speech_vector_ids:
            rs.speech_vector_ids.append(sv.id)

    # ── Root Speaker CRUD ────────────────────────────────────────────

    def create_root_speaker(
        self,
        label: str,
        color: str | None = None,
    ) -> RootSpeaker:
        rs_id = f"rs-{uuid.uuid4().hex[:12]}"
        rs = RootSpeaker(
            id=rs_id,
            label=label,
            color=color or "#9399b0",
        )
        self._root_speakers[rs_id] = rs
        logger.info(
            "root_speaker_created",
            session_id=self.session_id,
            rs_id=rs_id,
            label=label,
        )
        return rs

    def rename_root_speaker(self, rs_id: str, label: str) -> None:
        rs = self._root_speakers.get(rs_id)
        if rs is None:
            return
        rs.label = label
        logger.info(
            "root_speaker_renamed",
            session_id=self.session_id,
            rs_id=rs_id,
            label=label,
        )

    def delete_root_speaker(self, rs_id: str) -> None:
        rs = self._root_speakers.get(rs_id)
        if rs is None or rs.is_default:
            return

        # Reassign child vectors to "Candidate"
        for sv_id in list(rs.speech_vector_ids):
            sv = self._speech_vectors.get(sv_id)
            if sv:
                sv.root_speaker_id = self.DEFAULT_CANDIDATE_RS
                self._sv_to_rs[sv_id] = self.DEFAULT_CANDIDATE_RS
                candidate = self._root_speakers[self.DEFAULT_CANDIDATE_RS]
                if sv_id not in candidate.speech_vector_ids:
                    candidate.speech_vector_ids.append(sv_id)

        del self._root_speakers[rs_id]
        logger.info(
            "root_speaker_deleted",
            session_id=self.session_id,
            rs_id=rs_id,
        )

    def reassign_speech_vector(self, sv_id: str, target_rs_id: str) -> None:
        """Move an entire speech vector to a different root speaker."""
        sv = self._speech_vectors.get(sv_id)
        if sv is None:
            return
        target_rs = self._root_speakers.get(target_rs_id)
        if target_rs is None:
            return

        # Remove from old root speaker
        old_rs_id = sv.root_speaker_id
        old_rs = self._root_speakers.get(old_rs_id)
        if old_rs and sv_id in old_rs.speech_vector_ids:
            old_rs.speech_vector_ids.remove(sv_id)

        # Add to new root speaker
        sv.root_speaker_id = target_rs_id
        self._sv_to_rs[sv_id] = target_rs_id
        if sv_id not in target_rs.speech_vector_ids:
            target_rs.speech_vector_ids.append(sv_id)

        logger.info(
            "speech_vector_reassigned",
            session_id=self.session_id,
            sv_id=sv_id,
            from_rs=old_rs_id,
            to_rs=target_rs_id,
        )

    def reassign_transcript(
        self,
        transcript_id: str,
        source_sv_id: str,
        target_rs_id: str,
    ) -> str:
        """Reassign a single transcript to a different root speaker.

        Creates a virtual SpeechVector for the target root speaker and
        records the override so future lookups reflect the change.

        Returns the new speech_vector_id.
        """
        target_rs = self._root_speakers.get(target_rs_id)
        if target_rs is None:
            return source_sv_id

        # Create a virtual SpeechVector
        virt_id = f"sv-virt-{uuid.uuid4().hex[:12]}"
        virt_sv = SpeechVector(
            id=virt_id,
            root_speaker_id=target_rs_id,
            channel="virtual",
            is_virtual=True,
        )
        virt_sv.add_transcript(transcript_id)
        self._speech_vectors[virt_id] = virt_sv
        self._sv_to_rs[virt_id] = target_rs_id
        if virt_id not in target_rs.speech_vector_ids:
            target_rs.speech_vector_ids.append(virt_id)

        # Record the override
        self._transcript_sv_override[transcript_id] = virt_id

        logger.info(
            "transcript_reassigned",
            session_id=self.session_id,
            transcript_id=transcript_id,
            new_sv=virt_id,
            target_rs=target_rs_id,
        )
        return virt_id

    # ── Snapshot ─────────────────────────────────────────────────────

    def get_speaker_snapshot(self) -> dict:
        """Return the full speaker state for the frontend."""
        return {
            "root_speakers": {
                rs_id: rs.to_dict()
                for rs_id, rs in self._root_speakers.items()
            },
            "speech_vectors": {
                sv_id: sv.to_dict()
                for sv_id, sv in self._speech_vectors.items()
            },
        }

    # ── Calibration (preserved from legacy) ──────────────────────────

    def calibrate(
        self,
        interviewer_speaker_id: int,
        candidate_speaker_id: int | None = None,
    ) -> None:
        """Manually calibrate speaker mapping from the dashboard."""
        self._diarization_map[interviewer_speaker_id] = SpeakerRole.INTERVIEWER
        self.calibration.interviewer_deepgram_speaker = interviewer_speaker_id

        if candidate_speaker_id is not None:
            self._diarization_map[candidate_speaker_id] = SpeakerRole.CANDIDATE
            self.calibration.candidate_deepgram_speaker = candidate_speaker_id
        else:
            for spk_id in self._speaker_utterance_counts:
                if spk_id != interviewer_speaker_id and spk_id not in self._diarization_map:
                    self._diarization_map[spk_id] = SpeakerRole.CANDIDATE
                    if candidate_speaker_id is None:
                        self.calibration.candidate_deepgram_speaker = spk_id

        self.calibration.calibrated = True
        self.calibration.calibrated_at = datetime.now(UTC).isoformat()
        self.calibration.confidence = 0.9

        # Reassign speech vectors based on calibration
        self._reassign_vectors_after_calibration()

        logger.info(
            "speaker_calibrated",
            session_id=self.session_id,
            interviewer_speaker=interviewer_speaker_id,
            candidate_speaker=self.calibration.candidate_deepgram_speaker,
        )

    def _reassign_vectors_after_calibration(self) -> None:
        """After calibration, move speech vectors to match new mappings."""
        for sv in self._speech_vectors.values():
            if sv.is_virtual or sv.channel == "mic":
                continue
            if sv.deepgram_speaker_id is None:
                continue
            mapped_role = self._diarization_map.get(sv.deepgram_speaker_id)
            if mapped_role == SpeakerRole.INTERVIEWER:
                target = self.DEFAULT_INTERVIEWER_RS
            else:
                target = self.DEFAULT_CANDIDATE_RS
            if sv.root_speaker_id != target:
                self.reassign_speech_vector(sv.id, target)

    def auto_calibrate(self) -> bool:
        """Attempt automatic calibration based on speaking patterns."""
        if self.calibration.calibrated:
            return True
        if len(self._speaker_utterance_counts) < 2:
            return False

        sorted_speakers = sorted(
            self._speaker_utterance_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )
        candidate_id = sorted_speakers[0][0]
        interviewer_id = sorted_speakers[1][0]

        self.calibrate(interviewer_speaker_id=interviewer_id, candidate_speaker_id=candidate_id)
        self.calibration.confidence = 0.6
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
        self._speaker_utterance_counts[speaker_id] = (
            self._speaker_utterance_counts.get(speaker_id, 0) + 1
        )

    def _heuristic_role(self, speaker_id: int) -> SpeakerRole:
        if len(self._speaker_utterance_counts) <= 1:
            return SpeakerRole.CANDIDATE

        max_speaker = max(
            self._speaker_utterance_counts,
            key=self._speaker_utterance_counts.get,  # type: ignore
        )
        if speaker_id == max_speaker:
            return SpeakerRole.CANDIDATE
        else:
            return SpeakerRole.UNKNOWN

    # ── Serialization ────────────────────────────────────────────────

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
            "root_speakers": {
                rs_id: rs.to_dict()
                for rs_id, rs in self._root_speakers.items()
            },
            "speech_vectors": {
                sv_id: sv.to_dict()
                for sv_id, sv in self._speech_vectors.items()
            },
            "sv_to_rs": dict(self._sv_to_rs),
            "transcript_sv_override": dict(self._transcript_sv_override),
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

        # Restore root speakers (overwriting defaults if present)
        for rs_id, rs_data in data.get("root_speakers", {}).items():
            service._root_speakers[rs_id] = RootSpeaker.from_dict(rs_data)
        for sv_id, sv_data in data.get("speech_vectors", {}).items():
            service._speech_vectors[sv_id] = SpeechVector.from_dict(sv_data)
        service._sv_to_rs = dict(data.get("sv_to_rs", {}))
        service._transcript_sv_override = dict(data.get("transcript_sv_override", {}))

        return service
