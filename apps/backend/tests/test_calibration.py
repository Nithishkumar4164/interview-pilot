"""Tests for level calibration."""

import pytest

from src.schemas.session import InterviewLevel
from src.services.calibration_service import get_calibration_profile


class TestCalibrationProfiles:
    def test_all_levels_have_profiles(self):
        for level in InterviewLevel:
            profile = get_calibration_profile(level)
            assert profile.level == level

    def test_invalid_level_raises(self):
        with pytest.raises(ValueError, match="No calibration profile"):
            get_calibration_profile("nonexistent")  # type: ignore

    def test_difficulty_rises_with_level(self):
        lows = [get_calibration_profile(level).difficulty_range[0] for level in InterviewLevel]
        assert lows == sorted(lows)
        assert get_calibration_profile(InterviewLevel.INTERN).difficulty_range == (1, 2)
        assert get_calibration_profile(InterviewLevel.PRINCIPAL).difficulty_range == (4, 5)
