"""Ask the copilot (PEER_INTERVIEWER_ENABLED): the interviewer types a question, the copilot answers from live evidence.

Request/response beside the session graph, like runtime hints: it reads the
pipeline's current values and never waits behind a run. The answer comes
from a tool loop (TaskType.COPILOT_QUERY) over the same read-only evidence
tools the agentic report uses, built here from the live session instead of
the database: the transcript so far (T1..Tn), analyses, code, contradictions,
hints, facts, summary and the scorecard. Citations are validated against the
transcript, so an entry id in the reply is always real.
"""

from datetime import datetime
from typing import Any

from src.llm.failover import FailoverManager
from src.llm.interface import LLMConfig, TaskType
from src.llm.tools import run_tool_loop
from src.services.memory_service import FactStore
from src.services.report_agentic import EvidenceIndex, _elapsed, _json, _quote_matches, make_executor, tools_for
from src.utils.logging import get_logger

logger = get_logger("copilot_query")

COPILOT_SYSTEM_PROMPT = """You are the interviewer's copilot in a live interview. The interviewer just typed you a question.

Answer it from what actually happened: use the tools to search and read the transcript, the analyses, the code
timeline, contradictions, hints and the scorecard before you answer. Be brief and concrete; the interviewer is
reading while the candidate talks. When you state a fact about what was said, cite the transcript entry. If a
technical question has nothing to do with the transcript (e.g. "is quickselect O(n) worst case?"), answer it
directly from your own knowledge and say so.

Reply with JSON only:
{
  "answer": "<two to five sentences>",
  "citations": [{"entry_id": "T<number>", "quote": "<words copied from that entry>"}]
}"""

QUERY_CONFIG = LLMConfig(max_tokens=1500, temperature=0.2)
QUERY_MAX_STEPS = 4
MAX_QUESTION_CHARS = 500


def live_evidence_index(
    values: dict[str, Any],
    transcript_history: list[dict[str, Any]],
    code_values: dict[str, Any],
    target_level: str = "",
) -> EvidenceIndex:
    """The evidence index of the session so far, from the pipeline's live values."""
    finals = [t for t in transcript_history if t.get("is_final", True) and str(t.get("text", "")).strip()]
    start = None
    for t in finals:
        try:
            start = datetime.fromisoformat(str(t.get("timestamp", "")).replace("Z", "+00:00"))
            break
        except ValueError:
            continue
    entries = [
        {
            "id": f"T{i}",
            "elapsed": _elapsed(str(t.get("timestamp", "")), start),
            "speaker": t.get("speaker_role", "unknown"),
            "text": str(t.get("text", "")),
        }
        for i, t in enumerate(finals, start=1)
    ]
    facts = values.get("fact_store") or FactStore()
    last_code = code_values.get("last_result")
    code_timeline = [
        {"timestamp": s.timestamp, "line_count": s.line_count, "char_count": s.char_count}
        for s in code_values.get("history", [])
    ]
    if last_code is not None:
        code_timeline.append({"latest": last_code.to_dict(), "snapshots": code_values.get("snapshot_count", 0)})
    return EvidenceIndex(
        entries=entries,
        analyses=[
            {k: a.get(k) for k in ("answer_quality", "on_track", "insight", "red_flags", "hint_signal")}
            for a in values.get("analysis_digest", [])
        ],
        code_timeline=code_timeline,
        contradictions=[
            {
                "claim": c.claim,
                "new_statement": c.new_statement,
                "explanation": c.explanation,
                "confidence": c.confidence,
            }
            for c in values.get("contradictions", [])
        ],
        hints=list(facts.hint_interactions),
        hint_metrics={},
        facts_text=facts.to_text(),
        summary=values.get("rolling_summary", ""),
        target_level=target_level,
        duration_minutes=0,
        questions=[values["current_question"]] if values.get("current_question") else [],
        notes=list(values.get("scorecard", [])),
    )


def _citations(data: Any, index: EvidenceIndex) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for c in (data.get("citations") if isinstance(data, dict) else None) or []:
        if not isinstance(c, dict):
            continue
        entry = index.entry(str(c.get("entry_id", "")))
        if entry is None:
            continue
        quote = str(c.get("quote", "")).strip()
        if quote and not _quote_matches(quote, entry["text"]):
            quote = ""  # keep the pointer, drop a misquote
        out.append({"entry_id": entry["id"], "elapsed": entry["elapsed"], "speaker": entry["speaker"], "quote": quote})
    return out[:6]


async def answer_copilot_query(
    failover: FailoverManager, session_id: str, question: str, index: EvidenceIndex
) -> dict[str, Any]:
    """{"answer", "citations", "turns", "tool_calls"}. LLM errors propagate."""
    question = " ".join(question.split())[:MAX_QUESTION_CHARS]
    plan = f"Current question: {index.questions[0]}\n" if index.questions else ""
    user = f"{index.overview()}\n\n{plan}The interviewer asks: {question}"
    loop = await run_tool_loop(
        failover,
        TaskType.COPILOT_QUERY,
        COPILOT_SYSTEM_PROMPT,
        user,
        tools_for(index),
        make_executor(index),
        QUERY_CONFIG,
        session_id=session_id,
        max_steps=QUERY_MAX_STEPS,
        final_instruction="Stop using tools. Reply now with the JSON object only, as specified.",
    )
    data = _json(loop.text)
    answer = str(data.get("answer", "")).strip() if isinstance(data, dict) else ""
    if not answer:
        answer = loop.text.strip() or "I could not find an answer in the session so far."
    logger.info("copilot_query_answered", session_id=session_id, turns=loop.turns, tool_calls=loop.tool_calls)
    return {"answer": answer, "citations": _citations(data, index), "turns": loop.turns, "tool_calls": loop.tool_calls}
