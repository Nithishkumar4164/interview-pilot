"""The peer interviewer's scorecard and used cards reach the report (no Postgres)."""

import json

from src.llm.interface import ToolCall
from src.services.report_agentic import EVIDENCE_TOOLS, build_evidence_index, make_executor, tools_for
from src.services.report_generator import ReportGenerator, ReportInput


def _input(**extra) -> ReportInput:
    return ReportInput(
        session_id="s",
        duration_minutes=45,
        target_level="sde2",
        fact_store={},
        analysis_results=[],
        hint_events=[],
        hint_metrics={},
        contradictions=[],
        transcript_summary="",
        code_progress="",
        full_transcript=[
            {"speaker_role": "candidate", "text": "I would sort first.", "timestamp": "2026-01-01T00:00:00+00:00"}
        ],
        **extra,
    )


NOTES = [
    {
        "id": "u1:note",
        "criterion": "problem_solving",
        "quote": "I would sort first.",
        "note": "Reasonable start",
        "quality": 3,
        "source": "copilot",
        "question_id": "q1",
        "utterance_id": "u1",
        "at": "t",
    },
    {
        "id": "n1",
        "criterion": "behavioral",
        "quote": "",
        "note": "Owned the mistake quickly",
        "quality": None,
        "source": "interviewer",
        "question_id": "q1",
        "utterance_id": "",
        "at": "t",
    },
]


def test_single_call_report_context_lists_notes_and_used_cards() -> None:
    generator = ReportGenerator(failover_manager=None)  # type: ignore[arg-type]
    context = generator._build_context(
        _input(
            scorecard_notes=NOTES,
            copilot_usage=[{"kind": "probe", "text": "Why sort?"}, {"kind": "probe", "text": "x"}],
        )
    )
    assert "## Interviewer Scorecard Notes" in context
    assert "- [behavioral] (interviewer) Owned the mistake quickly" in context
    assert '- [problem_solving] Reasonable start "I would sort first."' in context
    assert "## Copilot Suggestions The Interviewer Used\n- probe: 2" in context
    # Without the peer interviewer the prompt is unchanged.
    plain = generator._build_context(_input())
    assert "Scorecard" not in plain and "Copilot" not in plain


async def test_agentic_report_offers_the_notes_tool_only_when_there_are_notes() -> None:
    without = build_evidence_index(_input())
    assert tools_for(without) == list(EVIDENCE_TOOLS)
    with_notes = build_evidence_index(_input(scorecard_notes=NOTES))
    assert [t.name for t in tools_for(with_notes)][-1] == "get_scorecard_notes"
    assert "Scorecard notes: 2 (1 written by the interviewer)" in with_notes.overview()
    execute = make_executor(with_notes)
    everything = json.loads(await execute(ToolCall(id="1", name="get_scorecard_notes", arguments={})))
    assert [n["criterion"] for n in everything] == ["problem_solving", "behavioral"]
    assert everything[0] == {
        "criterion": "problem_solving",
        "note": "Reasonable start",
        "quote": "I would sort first.",
        "quality": 3,
        "source": "copilot",
        "question_id": "q1",
    }
    only = json.loads(
        await execute(ToolCall(id="2", name="get_scorecard_notes", arguments={"criterion": "behavioral"}))
    )
    assert [n["source"] for n in only] == ["interviewer"]
    assert (
        await execute(ToolCall(id="3", name="get_scorecard_notes", arguments={"criterion": "system_design"}))
        == "No scorecard notes."
    )


def test_live_evidence_index_numbers_finals_and_carries_the_scorecard() -> None:
    from src.services.contradiction_service import Contradiction
    from src.services.copilot_query import live_evidence_index
    from src.services.memory_service import FactStore

    values = {
        "fact_store": FactStore(claimed_experience=["three years of Kafka"]),
        "analysis_digest": [
            {
                "answer_quality": 4,
                "on_track": True,
                "insight": "solid",
                "red_flags": [],
                "hint_signal": "none",
                "suggestion": "x",
            }
        ],
        "contradictions": [
            Contradiction(
                claim="a",
                new_statement="b",
                explanation="c",
                confidence=0.9,
                suggested_probe="d",
                detection_method="rule",
            )
        ],
        "rolling_summary": "So far so good.",
        "current_question": "Two sum",
        "scorecard": NOTES,
    }
    history = [
        {
            "id": "1",
            "speaker_role": "interviewer",
            "text": "Hi there",
            "is_final": True,
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
        {
            "id": "2",
            "speaker_role": "candidate",
            "text": "partial",
            "is_final": False,
            "timestamp": "2026-01-01T00:00:01+00:00",
        },
        {
            "id": "3",
            "speaker_role": "candidate",
            "text": "Hello, ready.",
            "is_final": True,
            "timestamp": "2026-01-01T00:01:05+00:00",
        },
    ]
    index = live_evidence_index(values, history, {"history": [], "last_result": None, "snapshot_count": 0})
    assert [(e["id"], e["speaker"], e["elapsed"]) for e in index.entries] == [
        ("T1", "interviewer", "00:00:00"),
        ("T2", "candidate", "00:01:05"),
    ]
    assert index.analyses == [
        {"answer_quality": 4, "on_track": True, "insight": "solid", "red_flags": [], "hint_signal": "none"}
    ]
    assert index.contradictions[0]["explanation"] == "c" and index.questions == ["Two sum"]
    assert "three years of Kafka" in index.facts_text and len(index.notes) == 2
    assert "Scorecard notes: 2" in index.overview()
