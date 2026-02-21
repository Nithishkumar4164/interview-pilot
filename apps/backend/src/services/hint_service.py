"""Progressive hint service.

Manages the 3-level progressive hint system:
  Level 1 (Nudge): General directional, Socratic question
  Level 2 (Guide): Specific concept pointer
  Level 3 (Direct): Near-explicit, stops short of answer

Tracks hint events for report scoring.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.schemas.hint import HintEventSchema, HintResponseMetricsSchema, HintResponseQuality
from src.utils.logging import get_logger

logger = get_logger("hint_service")

# Default auto-advance timeout (seconds)
DEFAULT_HINT_TIMEOUT_S = 180  # 3 minutes


@dataclass
class HintSet:
    """Pre-computed hints for a question (3 levels)."""

    question_id: str
    hints: dict[int, str] = field(default_factory=dict)  # level → text

    def get_hint(self, level: int) -> str | None:
        return self.hints.get(level)


@dataclass
class ActiveHint:
    """Currently active hint for a question."""

    hint_id: str
    question_id: str
    level: int
    text: str
    displayed_at: str
    interviewer_used: bool = False
    candidate_response_quality: HintResponseQuality = HintResponseQuality.NONE
    time_to_respond_ms: int | None = None


class HintService:
    """Manages progressive hints for an interview session.

    Usage:
        service = HintService(session_id)
        service.register_hints(question_id, {1: "hint1", 2: "hint2", 3: "hint3"})
        hint = service.activate_question(question_id)
        event = service.advance_hint(question_id)  # returns next level hint
    """

    def __init__(
        self,
        session_id: str,
        auto_advance_timeout_s: int = DEFAULT_HINT_TIMEOUT_S,
    ) -> None:
        self.session_id = session_id
        self.auto_advance_timeout_s = auto_advance_timeout_s

        # Pre-computed hints per question
        self._hint_sets: dict[str, HintSet] = {}

        # Current active hints
        self._active_question_id: str | None = None
        self._current_level: int = 0  # 0 = no hint shown yet

        # Event history
        self._hint_events: list[ActiveHint] = []

    def register_hints(
        self,
        question_id: str,
        hints: dict[int, str],
    ) -> None:
        """Register pre-computed hints for a question.

        Args:
            question_id: The question identifier
            hints: {1: "nudge text", 2: "guide text", 3: "direct text"}
        """
        self._hint_sets[question_id] = HintSet(
            question_id=question_id,
            hints=hints,
        )

    def activate_question(self, question_id: str) -> None:
        """Activate a question and reset hint state."""
        self._active_question_id = question_id
        self._current_level = 0

        logger.info(
            "question_activated",
            session_id=self.session_id,
            question_id=question_id,
            has_hints=question_id in self._hint_sets,
        )

    def advance_hint(self, question_id: str | None = None) -> ActiveHint | None:
        """Advance to the next hint level.

        Returns the new ActiveHint or None if no more hints available.
        """
        qid = question_id or self._active_question_id
        if not qid:
            return None

        hint_set = self._hint_sets.get(qid)
        if not hint_set:
            logger.warning("no_hints_for_question", question_id=qid)
            return None

        next_level = self._current_level + 1
        if next_level > 3:
            return None  # All hints exhausted

        hint_text = hint_set.get_hint(next_level)
        if not hint_text:
            return None

        self._current_level = next_level

        active = ActiveHint(
            hint_id=str(uuid.uuid4()),
            question_id=qid,
            level=next_level,
            text=hint_text,
            displayed_at=datetime.now(UTC).isoformat(),
        )
        self._hint_events.append(active)

        logger.info(
            "hint_advanced",
            session_id=self.session_id,
            question_id=qid,
            level=next_level,
        )

        return active

    def get_current_hint(self) -> ActiveHint | None:
        """Get the currently active hint."""
        if not self._hint_events:
            return None
        return self._hint_events[-1]

    def get_current_level(self) -> int:
        """Get the current hint level (0 = no hint shown)."""
        return self._current_level

    def acknowledge_hint(
        self,
        hint_id: str,
        interviewer_used: bool = True,
        candidate_response_quality: HintResponseQuality = HintResponseQuality.NONE,
        time_to_respond_ms: int | None = None,
    ) -> bool:
        """Acknowledge a hint with usage and response metrics."""
        for hint in self._hint_events:
            if hint.hint_id == hint_id:
                hint.interviewer_used = interviewer_used
                hint.candidate_response_quality = candidate_response_quality
                hint.time_to_respond_ms = time_to_respond_ms
                return True
        return False

    def get_metrics(self) -> HintResponseMetricsSchema:
        """Compute hint response metrics for the report."""
        total = len(self._hint_events)
        if total == 0:
            return HintResponseMetricsSchema(
                total_hints_given=0,
                average_response_time_ms=None,
                incorporation_rate=0.0,
                level_distribution={},
                independence_trajectory="stable",
            )

        # Response times
        response_times = [
            h.time_to_respond_ms
            for h in self._hint_events
            if h.time_to_respond_ms is not None
        ]
        avg_time = (sum(response_times) / len(response_times)) if response_times else None

        # Incorporation rate
        incorporated = sum(
            1 for h in self._hint_events
            if h.candidate_response_quality in (HintResponseQuality.PARTIAL, HintResponseQuality.FULL)
        )
        rate = incorporated / total

        # Level distribution
        level_dist: dict[int, int] = {}
        for h in self._hint_events:
            level_dist[h.level] = level_dist.get(h.level, 0) + 1

        # Independence trajectory
        trajectory = self._compute_trajectory()

        return HintResponseMetricsSchema(
            total_hints_given=total,
            average_response_time_ms=avg_time,
            incorporation_rate=rate,
            level_distribution=level_dist,
            independence_trajectory=trajectory,
        )

    def _compute_trajectory(self) -> str:
        """Compute whether the candidate is becoming more independent.

        Looks at hint levels over time — if later hints need lower levels,
        the candidate is improving.
        """
        if len(self._hint_events) < 3:
            return "stable"

        # Compare first half vs second half average levels
        mid = len(self._hint_events) // 2
        first_half_avg = sum(h.level for h in self._hint_events[:mid]) / mid
        second_half_avg = sum(h.level for h in self._hint_events[mid:]) / (len(self._hint_events) - mid)

        if second_half_avg < first_half_avg - 0.3:
            return "improving"
        elif second_half_avg > first_half_avg + 0.3:
            return "declining"
        return "stable"

    def to_schema_events(self) -> list[HintEventSchema]:
        """Convert hint events to schema objects."""
        return [
            HintEventSchema(
                id=h.hint_id,
                session_id=self.session_id,
                question_id=h.question_id,
                level=h.level,
                text=h.text,
                displayed_at=h.displayed_at,
                interviewer_used=h.interviewer_used,
                candidate_response_quality=h.candidate_response_quality,
                time_to_respond_ms=h.time_to_respond_ms,
            )
            for h in self._hint_events
        ]

    def record_runtime_hint_feedback(
        self,
        hint_id: str,
        question_id: str,
        text: str,
        quality: str,
    ) -> None:
        """Record runtime hint events alongside static ones for unified metrics."""
        active = ActiveHint(
            hint_id=hint_id,
            question_id=question_id,
            level=0,  # 0 indicates runtime hint
            text=text,
            displayed_at=datetime.now(UTC).isoformat(),
            interviewer_used=True,
            candidate_response_quality=HintResponseQuality(quality) if quality in ("none", "partial", "full") else HintResponseQuality.NONE,
        )
        self._hint_events.append(active)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "active_question_id": self._active_question_id,
            "current_level": self._current_level,
            "hint_events": [
                {
                    "hint_id": h.hint_id,
                    "question_id": h.question_id,
                    "level": h.level,
                    "text": h.text,
                    "displayed_at": h.displayed_at,
                    "interviewer_used": h.interviewer_used,
                    "candidate_response_quality": h.candidate_response_quality.value,
                    "time_to_respond_ms": h.time_to_respond_ms,
                }
                for h in self._hint_events
            ],
            "metrics": self.get_metrics().model_dump(),
        }
