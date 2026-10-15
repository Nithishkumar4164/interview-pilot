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
