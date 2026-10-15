"""Deterministic peer-interviewer helpers (no LLM, no Postgres)."""

import pytest

from src.services.peer_interviewer import (
    CRITERIA,
    copilot_card,
    coverage_increments,
    coverage_payload,
    difficulty_card,
    difficulty_signal,
    looks_like_a_question,
    monologue_card,
    pace_alert,
    pace_card,
    seconds_between,
    talk_share,
    talk_share_card,
    word_count,
)


def test_talk_share_is_the_interviewer_share_of_words() -> None:
    assert talk_share([]) == (0.0, 0)
    share, total = talk_share([["interviewer", 60], ["candidate", 20], ["interviewer", 20]])
    assert (round(share, 2), total) == (0.8, 100)


def test_seconds_between_is_infinite_when_a_timestamp_is_missing() -> None:
    assert seconds_between("2026-01-01T00:00:00+00:00", "2026-01-01T00:05:30+00:00") == 330.0
    assert seconds_between("", "2026-01-01T00:00:00+00:00") == float("inf")
    assert seconds_between("not a time", "2026-01-01T00:00:00Z") == float("inf")


@pytest.mark.parametrize(
    ("text", "asking"),
    [
        ("Can I assume the input fits in memory", True),
        ("What should happen on an empty array?", True),
        ("I will use a hash map and scan once.", False),
    ],
)
def test_looks_like_a_question(text: str, asking: bool) -> None:
    assert looks_like_a_question(text) is asking


def test_cards_have_one_shape_and_refuse_unknown_kinds() -> None:
    card = talk_share_card("s:u:talk_share", 0.654, 300, "2026-01-01T00:00:00+00:00")
    assert card["kind"] == "coaching" and card["source"] == "rule" and card["priority"] == "info"
    assert card["title"] == "You have spoken 65% of the last 300 words"
    assert set(card) == {
        "id",
        "kind",
        "priority",
        "title",
        "body",
        "items",
        "probe",
        "question_id",
        "source",
        "timestamp",
    }
    assert monologue_card("s:u:monologue", 131, "2026-01-01T00:00:00+00:00")["title"].endswith("(131 words)")
    with pytest.raises(AssertionError):
        copilot_card("x", "weather", "t", "b")
    assert word_count("  a  b c ") == 3


def test_pace_alerts_fire_once_per_threshold_highest_first() -> None:
    assert pace_alert(30, 60, []) is None
    assert pace_alert(60, 60, []) == "over"
    assert pace_alert(61, 60, ["over"]) is None
    assert pace_alert(95, 60, ["over"]) == "far_over"
    assert pace_alert(95, 60, []) == "far_over"  # a long gap between ticks skips straight to the higher alert
    assert pace_alert(10_000, 0, []) is None  # no budget, no alert
    card = pace_card("s:q1:pace:over", "over", 125, 60, 2, 25, "q1", "2026-01-01T00:02:05+00:00")
    assert card["title"] == "1 min over this question's 1 min budget"
    assert card["body"].startswith("2 questions and 25 min of plan left.")
    assert pace_card("s:q:pace:far_over", "far_over", 200, 60, 0, 0, "q", "")["body"].startswith(
        "This is the last planned question."
    )


def _analysis(quality: int, on_track: bool = True, hint_signal: str = "none", dims: bool = True):
    from src.schemas.analysis import EnhancedAnalysisResult
    from src.services.analysis_service import parse_analysis

    data = {"answer_quality": quality, "on_track": on_track, "hint_signal": hint_signal}
    if dims:
        data["dimensions"] = {
            "relevance": {"score": "good", "on_topic": True, "justification": ""},
            "behavioral_signal": {"score": "good", "signals_detected": ["ownership"], "flags": [], "justification": ""},
            "contextual_consistency": {"score": "good", "consistent": True, "contradictions": [], "justification": ""},
            "hint_reception": {
                "score": "good",
                "acted_on_hint": True,
                "incorporation_level": "full",
                "justification": "",
            },
            "depth_completeness": {
                "score": "good",
                "key_points_covered": [],
                "key_points_missed": [],
                "justification": "",
            },
            "communication_quality": {
                "score": "good",
                "clarity": "high",
                "structure": "high",
                "confidence": "high",
                "justification": "",
            },
        }
    import json

    result: EnhancedAnalysisResult = parse_analysis(json.dumps(data), None)
    result.answer_quality = quality  # parse_analysis clamps to 1-5; 0 marks a failed analysis
    return result


def test_coverage_increments_follow_the_question_category_and_the_dimensions() -> None:
    assert coverage_increments(_analysis(0), "coding", True) == []
    assert coverage_increments(_analysis(4, dims=False), "coding", False) == ["problem_solving"]
    assert coverage_increments(_analysis(4), "coding", True) == [
        "problem_solving",
        "communication",
        "hint_responsiveness",
        "behavioral",
        "code_quality",
    ]
    assert "system_design" in coverage_increments(_analysis(3), "system_design", False)
    assert "code_quality" not in coverage_increments(_analysis(3), "coding", False)  # no code written yet


def test_coverage_payload_reports_status_and_gaps() -> None:
    payload = coverage_payload({"problem_solving": 3, "communication": 1}, claims=2, contradictions=0, timestamp="t")
    assert set(payload["criteria"]) == set(CRITERIA)
    assert payload["criteria"]["problem_solving"] == {"evidence": 3, "status": "solid"}
    assert payload["criteria"]["communication"]["status"] == "thin"
    assert payload["criteria"]["resume_verification"] == {"evidence": 2, "status": "thin"}
    assert payload["gaps"] == ["code_quality", "hint_responsiveness", "system_design", "behavioral"]


def test_difficulty_signal_needs_three_scored_answers() -> None:
    assert difficulty_signal([_analysis(5), _analysis(5)]) is None
    assert difficulty_signal([_analysis(5), _analysis(0), _analysis(4), _analysis(5)]) == "cruising"
    assert difficulty_signal([_analysis(5), _analysis(4, on_track=False), _analysis(5)]) is None
    assert difficulty_signal([_analysis(2), _analysis(3), _analysis(1)]) == "struggling"
    assert (
        difficulty_signal([_analysis(3, hint_signal="direct"), _analysis(3), _analysis(3, hint_signal="direct")])
        == "struggling"
    )
    assert difficulty_signal([_analysis(3), _analysis(3), _analysis(3)]) is None
    card = difficulty_card(
        "s:q1:difficulty:cruising", "cruising", {"id": "q1", "alternates": ["Harder variant", "", "Third"]}, "t"
    )
    assert card["items"] == ["Harder variant", "Third"] and card["question_id"] == "q1" and card["source"] == "rule"
