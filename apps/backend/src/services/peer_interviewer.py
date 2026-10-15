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


# ── Claim verification ─────────────────────────────────────────────────────

CLAIM_SYSTEM_PROMPT = """Verify a technical claim made in a live interview. Return JSON.

The candidate just stated something technical as fact: a complexity, a guarantee, how an algorithm, data
structure, protocol or tool behaves. Decide whether it is right. Be strict about what was actually claimed and
generous about imprecise wording. If the statement is an opinion, a plan, or cannot be checked, say so.

Respond in JSON:
{
  "claim": "<the claim, in one sentence>",
  "verdict": "<correct|incorrect|misleading|unverifiable>",
  "confidence": <0.0-1.0>,
  "correction": "<what is actually true, one or two sentences; empty if correct>",
  "self_correct_probe": "<a question the interviewer can ask so the candidate finds the error themselves; empty if correct>"
}"""

CLAIM_MIN_CONFIDENCE = 0.6


async def verify_claim(
    failover: FailoverManager,
    session_id: str,
    context_text: str,
    question_plan: dict | None,
    speaker: str,
    text: str,
) -> dict[str, Any] | None:
    """A wrong or misleading claim with its correction, or None. LLM errors propagate."""
    user_content = (
        f"{context_text}{build_question_plan_context(question_plan)}\n\n## Statement To Verify\n[{speaker}]: {text}"
    )
    result = await failover.analyze(
        AnalysisContext(
            system_prompt=CLAIM_SYSTEM_PROMPT, user_content=user_content, task_type=TaskType.PEER_INTERVIEWER
        ),
        LLMConfig(max_tokens=400, temperature=0.1, response_format="json"),
        session_id=session_id,
    )
    data = _parsed(result.content, result.parsed)
    verdict = str(data.get("verdict", "")).lower()
    try:
        confidence = float(data.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if verdict not in ("incorrect", "misleading") or confidence < CLAIM_MIN_CONFIDENCE:
        return None
    return {
        "claim": str(data.get("claim", "")) or text[:160],
        "verdict": verdict,
        "confidence": confidence,
        "correction": str(data.get("correction", "")),
        "self_correct_probe": str(data.get("self_correct_probe", "")),
    }


# ── Clarification watch ────────────────────────────────────────────────────

CLARIFY_SYSTEM_PROMPT = """Draft a clarification for a live interview. Return JSON.

Decide whether the candidate needs something cleared up right now: they asked a question the interviewer has
not answered, or they seem to have misread the problem (wrong input assumptions, solving a different task).
If so, draft the reply the interviewer can give. The reply clarifies the problem statement only; it must not
hint at the solution.

Respond in JSON:
{
  "needs_clarification": true/false,
  "what_is_unclear": "<the open question or misunderstanding, one sentence; empty if none>",
  "reply": "<what the interviewer can say, in their voice; empty if none>"
}"""


async def draft_clarification(
    failover: FailoverManager,
    session_id: str,
    context_text: str,
    question_plan: dict | None,
    speaker: str,
    text: str,
) -> dict[str, str] | None:
    """{"what_is_unclear", "reply"} when the candidate needs a clarification, else None. LLM errors propagate."""
    user_content = (
        f"{context_text}{build_question_plan_context(question_plan)}\n\n## Latest Utterance\n[{speaker}]: {text}"
    )
    result = await failover.analyze(
        AnalysisContext(
            system_prompt=CLARIFY_SYSTEM_PROMPT, user_content=user_content, task_type=TaskType.PEER_INTERVIEWER
        ),
        LLMConfig(max_tokens=300, temperature=0.2, response_format="json"),
        session_id=session_id,
    )
    data = _parsed(result.content, result.parsed)
    reply = str(data.get("reply", "")).strip()
    if not data.get("needs_clarification") or not reply:
        return None
    return {"what_is_unclear": str(data.get("what_is_unclear", "")), "reply": reply}


def looks_like_a_question(text: str) -> bool:
    """A candidate utterance that is asking the interviewer something (cheap trigger for the clarification check)."""
    stripped = text.strip()
    return "?" in stripped or stripped.lower().startswith(("can i assume", "should i", "do you want", "is it okay if"))
