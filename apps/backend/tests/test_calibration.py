"""Tests for the calibration engine."""

import pytest

from src.schemas.session import InterviewLevel
from src.services.calibration_service import (
    CALIBRATION_PROFILES,
    CalibrationProfile,
    compute_time_allocation,
    get_calibration_profile,
)


class TestCalibrationProfiles:
    def test_all_levels_have_profiles(self):
        for level in InterviewLevel:
            profile = get_calibration_profile(level)
            assert profile.level == level

    def test_invalid_level_raises(self):
        with pytest.raises(ValueError, match="No calibration profile"):
            get_calibration_profile("nonexistent")  # type: ignore

    def test_intern_profile_easy_difficulty(self):
        profile = get_calibration_profile(InterviewLevel.INTERN)
        assert profile.difficulty_range == (1, 2)
        assert profile.expected_question_count == 3

    def test_senior_profile_harder_difficulty(self):
        profile = get_calibration_profile(InterviewLevel.SENIOR)
        assert profile.difficulty_range[0] >= 3
        assert any(p.phase == "system_design" for p in profile.phases)

    def test_principal_profile_system_design_heavy(self):
        profile = get_calibration_profile(InterviewLevel.PRINCIPAL)
        sd_phase = next(p for p in profile.phases if p.phase == "system_design")
        assert sd_phase.fraction >= 0.4

    def test_phase_fractions_sum_to_roughly_1(self):
        for level in InterviewLevel:
            profile = get_calibration_profile(level)
            total = sum(p.fraction for p in profile.phases) + profile.buffer_fraction
            assert 0.9 <= total <= 1.1, f"Level {level}: fractions sum to {total}"


class TestTimeAllocation:
    def test_45min_sde2_allocation(self):
        profile = get_calibration_profile(InterviewLevel.SDE2)
        alloc = compute_time_allocation(profile, 45)

        assert alloc.total_minutes == 45
        assert alloc.intro_minutes == profile.intro_minutes
        assert alloc.wrap_up_minutes == profile.wrap_up_minutes
        assert alloc.buffer_minutes > 0
        assert alloc.available_minutes > 0
        assert sum(alloc.phase_minutes.values()) == alloc.available_minutes

    def test_60min_senior_allocation(self):
        profile = get_calibration_profile(InterviewLevel.SENIOR)
        alloc = compute_time_allocation(profile, 60)

        assert "coding" in alloc.phase_minutes
        assert "system_design" in alloc.phase_minutes
        assert "behavioral" in alloc.phase_minutes

    def test_too_short_session_raises(self):
        profile = get_calibration_profile(InterviewLevel.SDE2)
        with pytest.raises(ValueError, match="Session too short"):
            compute_time_allocation(profile, 5)

    def test_minimum_viable_session(self):
        profile = get_calibration_profile(InterviewLevel.INTERN)
        # Needs at least intro + wrap_up + 1 min
        min_minutes = profile.intro_minutes + profile.wrap_up_minutes + 2
        alloc = compute_time_allocation(profile, min_minutes)
        assert alloc.available_minutes > 0

    def test_each_phase_gets_at_least_1_minute(self):
        profile = get_calibration_profile(InterviewLevel.SDE2)
        alloc = compute_time_allocation(profile, 30)
        for phase_name, minutes in alloc.phase_minutes.items():
            assert minutes >= 1, f"Phase {phase_name} got {minutes} minutes"
