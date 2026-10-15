"""Deterministic peer-interviewer helpers (no LLM, no Postgres)."""

import pytest

from src.services.peer_interviewer import (
    copilot_card,
    looks_like_a_question,
    monologue_card,
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
