"""Level calibration engine.

Maps interview level → parameters (question count, difficulty range,
time allocation, topic mix). Pure rules-based — no LLM calls.
"""

from dataclasses import dataclass, field

from src.schemas.session import InterviewLevel


@dataclass(frozen=True)
class PhaseAllocation:
    """Time and question allocation for a single interview phase."""

    phase: str
    fraction: float  # fraction of total time
    categories: list[str] = field(default_factory=list)
    min_questions: int = 1
    max_questions: int = 3


@dataclass(frozen=True)
class CalibrationProfile:
    """Complete calibration profile for a given interview level."""

    level: InterviewLevel
    difficulty_range: tuple[int, int]  # (min, max) on 1-5 scale
    expected_question_count: int
    phases: list[PhaseAllocation]
    buffer_fraction: float = 0.10  # 10% buffer by default
    intro_minutes: int = 3
    wrap_up_minutes: int = 5
    preferred_tags: list[str] = field(default_factory=list)
    avoid_tags: list[str] = field(default_factory=list)


# ── Calibration profiles per level ───────────────────────────────────────────

CALIBRATION_PROFILES: dict[InterviewLevel, CalibrationProfile] = {
    InterviewLevel.INTERN: CalibrationProfile(
        level=InterviewLevel.INTERN,
        difficulty_range=(1, 2),
        expected_question_count=3,
        intro_minutes=5,
        wrap_up_minutes=5,
        phases=[
            PhaseAllocation(
                phase="coding",
                fraction=0.70,
                categories=["coding"],
                min_questions=2,
                max_questions=2,
            ),
            PhaseAllocation(
                phase="behavioral",
                fraction=0.20,
                categories=["behavioral"],
                min_questions=1,
                max_questions=1,
            ),
        ],
        preferred_tags=["fundamentals", "arrays", "strings"],
    ),
    InterviewLevel.SDE1: CalibrationProfile(
        level=InterviewLevel.SDE1,
        difficulty_range=(1, 3),
        expected_question_count=4,
        phases=[
            PhaseAllocation(
                phase="coding",
                fraction=0.60,
                categories=["coding"],
                min_questions=2,
                max_questions=3,
            ),
            PhaseAllocation(
                phase="system_design",
                fraction=0.15,
                categories=["system_design"],
                min_questions=0,
                max_questions=1,
            ),
            PhaseAllocation(
                phase="behavioral",
                fraction=0.15,
                categories=["behavioral"],
                min_questions=1,
                max_questions=1,
            ),
        ],
        preferred_tags=["data_structures", "algorithms"],
    ),
    InterviewLevel.SDE2: CalibrationProfile(
        level=InterviewLevel.SDE2,
        difficulty_range=(2, 4),
        expected_question_count=4,
        phases=[
            PhaseAllocation(
                phase="coding",
                fraction=0.50,
                categories=["coding"],
                min_questions=1,
                max_questions=2,
            ),
            PhaseAllocation(
                phase="system_design",
                fraction=0.25,
                categories=["system_design"],
                min_questions=1,
                max_questions=1,
            ),
            PhaseAllocation(
                phase="behavioral",
                fraction=0.15,
                categories=["behavioral"],
                min_questions=1,
                max_questions=2,
            ),
        ],
        preferred_tags=["design_patterns", "scalability"],
    ),
    InterviewLevel.SENIOR: CalibrationProfile(
        level=InterviewLevel.SENIOR,
        difficulty_range=(3, 5),
        expected_question_count=4,
        phases=[
            PhaseAllocation(
                phase="coding",
                fraction=0.40,
                categories=["coding"],
                min_questions=1,
                max_questions=2,
            ),
            PhaseAllocation(
                phase="system_design",
                fraction=0.35,
                categories=["system_design"],
                min_questions=1,
                max_questions=2,
            ),
            PhaseAllocation(
                phase="behavioral",
                fraction=0.15,
                categories=["behavioral", "leadership"],
                min_questions=1,
                max_questions=1,
            ),
        ],
        preferred_tags=["architecture", "distributed_systems", "tradeoffs"],
    ),
    InterviewLevel.STAFF: CalibrationProfile(
        level=InterviewLevel.STAFF,
        difficulty_range=(3, 5),
        expected_question_count=4,
        phases=[
            PhaseAllocation(
                phase="system_design",
                fraction=0.45,
                categories=["system_design"],
                min_questions=1,
                max_questions=2,
            ),
            PhaseAllocation(
                phase="coding",
                fraction=0.25,
                categories=["coding"],
                min_questions=1,
                max_questions=1,
            ),
            PhaseAllocation(
                phase="behavioral",
                fraction=0.20,
                categories=["behavioral", "leadership"],
                min_questions=1,
                max_questions=2,
            ),
        ],
        preferred_tags=["architecture", "cross_team", "technical_strategy"],
    ),
    InterviewLevel.PRINCIPAL: CalibrationProfile(
        level=InterviewLevel.PRINCIPAL,
        difficulty_range=(4, 5),
        expected_question_count=3,
        phases=[
            PhaseAllocation(
                phase="system_design",
                fraction=0.50,
                categories=["system_design"],
                min_questions=1,
                max_questions=2,
            ),
            PhaseAllocation(
                phase="behavioral",
                fraction=0.30,
                categories=["behavioral", "leadership", "strategy"],
                min_questions=1,
                max_questions=2,
            ),
            PhaseAllocation(
                phase="coding",
                fraction=0.10,
                categories=["coding"],
                min_questions=0,
                max_questions=1,
            ),
        ],
        preferred_tags=["org_wide_impact", "technical_vision", "mentorship"],
    ),
}


def get_calibration_profile(level: InterviewLevel) -> CalibrationProfile:
    """Return the calibration profile for a given interview level."""
    profile = CALIBRATION_PROFILES.get(level)
    if profile is None:
        raise ValueError(f"No calibration profile for level: {level}")
    return profile


@dataclass
class TimeAllocation:
    """Computed time allocation for a specific interview session."""

    total_minutes: int
    intro_minutes: int
    wrap_up_minutes: int
    buffer_minutes: int
    available_minutes: int  # total - intro - wrap_up - buffer
    phase_minutes: dict[str, int]  # phase name → allocated minutes


def compute_time_allocation(
    profile: CalibrationProfile,
    total_minutes: int,
) -> TimeAllocation:
    """Compute per-phase time allocation given a calibration profile and session duration."""
    buffer_minutes = int(total_minutes * profile.buffer_fraction)
    available = total_minutes - profile.intro_minutes - profile.wrap_up_minutes - buffer_minutes

    if available <= 0:
        raise ValueError(
            f"Session too short: {total_minutes}min total, "
            f"need at least {profile.intro_minutes + profile.wrap_up_minutes + 1}min "
            f"for intro/wrap-up alone."
        )

    phase_minutes: dict[str, int] = {}
    allocated = 0
    for i, phase in enumerate(profile.phases):
        if i == len(profile.phases) - 1:
            # Last phase gets the remainder to avoid rounding loss
            minutes = available - allocated
        else:
            minutes = int(available * phase.fraction)
        phase_minutes[phase.phase] = max(minutes, 1)
        allocated += phase_minutes[phase.phase]

    return TimeAllocation(
        total_minutes=total_minutes,
        intro_minutes=profile.intro_minutes,
        wrap_up_minutes=profile.wrap_up_minutes,
        buffer_minutes=buffer_minutes,
        available_minutes=available,
        phase_minutes=phase_minutes,
    )
