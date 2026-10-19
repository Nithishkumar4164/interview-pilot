"""Level calibration.

Maps interview level → the difficulty range its questions are written at.
Pure rules-based — no LLM calls.
"""

from dataclasses import dataclass

from src.schemas.session import InterviewLevel


@dataclass(frozen=True)
class CalibrationProfile:
    """Calibration for a given interview level."""

    level: InterviewLevel
    difficulty_range: tuple[int, int]  # (min, max) on 1-5 scale


CALIBRATION_PROFILES: dict[InterviewLevel, CalibrationProfile] = {
    InterviewLevel.INTERN: CalibrationProfile(level=InterviewLevel.INTERN, difficulty_range=(1, 2)),
    InterviewLevel.SDE1: CalibrationProfile(level=InterviewLevel.SDE1, difficulty_range=(1, 3)),
    InterviewLevel.SDE2: CalibrationProfile(level=InterviewLevel.SDE2, difficulty_range=(2, 4)),
    InterviewLevel.SENIOR: CalibrationProfile(level=InterviewLevel.SENIOR, difficulty_range=(3, 5)),
    InterviewLevel.STAFF: CalibrationProfile(level=InterviewLevel.STAFF, difficulty_range=(3, 5)),
    InterviewLevel.PRINCIPAL: CalibrationProfile(level=InterviewLevel.PRINCIPAL, difficulty_range=(4, 5)),
}


def get_calibration_profile(level: InterviewLevel) -> CalibrationProfile:
    """Return the calibration profile for a given interview level."""
    profile = CALIBRATION_PROFILES.get(level)
    if profile is None:
        raise ValueError(f"No calibration profile for level: {level}")
    return profile
