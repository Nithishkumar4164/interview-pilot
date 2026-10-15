"""Peer interviewer (PEER_INTERVIEWER_ENABLED): what the copilot says to the interviewer.

Everything here is advice for the interviewer, never a message to the
candidate. The session graph decides *when* to call these (the analysis
chooses follow-up actions, deterministic signals fire on their own) and
emits the results as copilot cards; this module owns the prompts, the
parsing and the deterministic signal rules.

LLM calls raise on provider errors: the graph node decides on retries and
timeouts, and a failed follow-up never fails the run.
"""

import json
from datetime import UTC, datetime
from typing import Any

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.schemas.analysis import EnhancedAnalysisResult
from src.services.analysis_service import build_question_plan_context
from src.utils.logging import get_logger

logger = get_logger("peer_interviewer")

# ── Copilot cards ──────────────────────────────────────────────────────────

CARD_KINDS = ("probe", "claim_check", "clarification", "coaching", "pace", "stuck", "difficulty", "edge_cases")
PRIORITIES = ("info", "suggest", "urgent")


def copilot_card(
    card_id: str,
    kind: str,
    title: str,
    body: str,
    *,
    items: list[str] | None = None,
    probe: str = "",
    priority: str = "suggest",
    source: str = "llm",
    question_id: str = "",
    timestamp: str = "",
) -> dict[str, Any]:
    """The one payload shape every whisper to the interviewer uses (``send_copilot_card``)."""
    assert kind in CARD_KINDS and priority in PRIORITIES
    return {
        "id": card_id,
        "kind": kind,
        "priority": priority,
        "title": title,
        "body": body,
        "items": list(items or []),
        "probe": probe,
        "question_id": question_id,
        "source": source,
        "timestamp": timestamp or datetime.now(UTC).isoformat(),
    }


def _parsed(content: str, parsed: dict | None) -> dict:
    if isinstance(parsed, dict):
        return parsed
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _analysis_summary(analysis: EnhancedAnalysisResult | None) -> str:
    if analysis is None:
        return ""
    lines = [
        f"Quality {analysis.answer_quality}/5, {'on track' if analysis.on_track else 'off track'}: {analysis.insight}"
    ]
    dims = analysis.dimensions
    if dims is not None:
        if dims.depth_completeness.key_points_missed:
            lines.append("Missed: " + "; ".join(dims.depth_completeness.key_points_missed))
        if dims.depth_completeness.key_points_covered:
            lines.append("Covered: " + "; ".join(dims.depth_completeness.key_points_covered))
        weakest = min(
            (
                ("relevance", dims.relevance.score),
                ("depth", dims.depth_completeness.score),
                ("communication", dims.communication_quality.score),
                ("consistency", dims.contextual_consistency.score),
            ),
            key=lambda d: _SCORE_RANK.get(d[1].value, 2),
        )
        lines.append(f"Weakest dimension: {weakest[0]} ({weakest[1].value})")
    return "\n".join(lines)


_SCORE_RANK = {"poor": 0, "weak": 1, "adequate": 2, "good": 3, "excellent": 4}


# ── Suggested probes ───────────────────────────────────────────────────────

PROBES_SYSTEM_PROMPT = """Suggest follow-up questions for a live interview. Return JSON.

You are a senior engineer sitting beside the interviewer. The candidate just answered. Propose 1-3 follow-up
questions the interviewer could ask next, each aimed at something the answer left open: a gap in the expected
outline, a claim that deserves evidence, or the weakest part of the answer. Prefer questions that make the
candidate reason aloud over yes/no questions. Never reveal the expected answer in the question.

Respond in JSON:
{
  "probes": [
    {"question": "<what the interviewer can ask, in their voice>", "targets": "<the gap or claim it tests>", "why": "<one short reason>"}
  ]
}
Return "probes": [] if the answer was complete and nothing is worth probing."""

MAX_PROBES = 3


async def suggest_probes(
    failover: FailoverManager,
    session_id: str,
    context_text: str,
    question_plan: dict | None,
    analysis: EnhancedAnalysisResult | None,
    speaker: str,
    text: str,
) -> list[dict[str, str]]:
    """Up to three follow-up questions for the interviewer, or []. LLM errors propagate."""
    summary = _analysis_summary(analysis)
    user_content = (
        f"{context_text}{build_question_plan_context(question_plan)}"
        + (f"\n\n## Analysis Of This Answer\n{summary}" if summary else "")
        + f"\n\n## Latest Utterance\n[{speaker}]: {text}"
    )
    result = await failover.analyze(
        AnalysisContext(
            system_prompt=PROBES_SYSTEM_PROMPT, user_content=user_content, task_type=TaskType.PEER_INTERVIEWER
        ),
        LLMConfig(max_tokens=500, temperature=0.4, response_format="json"),
        session_id=session_id,
    )
    probes: list[dict[str, str]] = []
    for item in _parsed(result.content, result.parsed).get("probes") or []:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        if question:
            probes.append(
                {"question": question, "targets": str(item.get("targets", "")), "why": str(item.get("why", ""))}
            )
    return probes[:MAX_PROBES]
