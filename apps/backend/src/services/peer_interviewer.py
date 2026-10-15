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


# ── Interviewer coaching ───────────────────────────────────────────────────

COACHING_SYSTEM_PROMPT = """Coach the interviewer of a live technical interview. Return JSON.

You see the interviewer's latest turn and the conversation before it. Point out only clear problems with how
the question was asked, never with the candidate:
  leading_question   - the question suggests the answer or the approach ("so you'd use a hash map, right?");
  revealed_answer    - the interviewer gave away a key step of the expected solution;
  unclear_question   - the question is ambiguous or missing constraints the candidate needs;
  stacked_questions  - several questions at once, so the candidate can only answer one;
  interrupting       - the interviewer cut off an answer that was still developing.
Most turns are fine: return an empty list unless you are confident.

Respond in JSON:
{
  "issues": [
    {"kind": "<one of the kinds above>", "note": "<what happened, one sentence>", "suggestion": "<how to rephrase or recover, one sentence>"}
  ]
}"""

COACHING_KINDS = ("leading_question", "revealed_answer", "unclear_question", "stacked_questions", "interrupting")
COACHING_MIN_WORDS = 12  # shorter interviewer turns ("okay", "go on") are not worth a call
COACHING_MIN_GAP_S = 20.0  # at most one coaching call per 20 s of interview time


async def coach_interviewer(
    failover: FailoverManager, session_id: str, context_text: str, question: str, text: str
) -> list[dict[str, str]]:
    """Problems with the interviewer's latest turn (usually none). LLM errors propagate."""
    user_content = (
        f"{context_text}"
        + (f"\n\n## Planned Question\n{question}" if question else "")
        + f"\n\n## Interviewer's Latest Turn\n[interviewer]: {text}"
    )
    result = await failover.analyze(
        AnalysisContext(
            system_prompt=COACHING_SYSTEM_PROMPT, user_content=user_content, task_type=TaskType.INTERVIEWER_COACHING
        ),
        LLMConfig(max_tokens=300, temperature=0.1, response_format="json"),
        session_id=session_id,
    )
    issues: list[dict[str, str]] = []
    for item in _parsed(result.content, result.parsed).get("issues") or []:
        if not isinstance(item, dict) or item.get("kind") not in COACHING_KINDS:
            continue
        note = str(item.get("note", "")).strip()
        if note:
            issues.append({"kind": str(item["kind"]), "note": note, "suggestion": str(item.get("suggestion", ""))})
    return issues[:3]


# ── Deterministic coaching signals ─────────────────────────────────────────

TALK_WINDOW_SIZE = 40  # utterances the talk-share window covers
TALK_SHARE_MIN_WORDS = 250  # no judgment on talk share before this many words were spoken in the window
TALK_SHARE_THRESHOLD = 0.6  # interviewer share of words that earns a nudge
TALK_CARD_MIN_GAP_S = 300.0  # at most one talk-share nudge per 5 minutes
MONOLOGUE_WORDS = 120  # one interviewer turn this long is a monologue


def word_count(text: str) -> int:
    return len(text.split())


def talk_share(window: list[list]) -> tuple[float, int]:
    """(interviewer share of words, total words) over ``window`` rows of ``[speaker, words]``."""
    total = sum(int(w) for _, w in window)
    if total == 0:
        return 0.0, 0
    interviewer = sum(int(w) for s, w in window if s == "interviewer")
    return interviewer / total, total


def seconds_between(earlier: str, later: str) -> float:
    """Interview time between two ISO timestamps; infinite when either is missing or unparsable."""
    try:
        a = datetime.fromisoformat(earlier.replace("Z", "+00:00"))
        b = datetime.fromisoformat(later.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return float("inf")
    return (b - a).total_seconds()


def talk_share_card(card_id: str, share: float, total: int, timestamp: str) -> dict[str, Any]:
    pct = round(share * 100)
    return copilot_card(
        card_id,
        "coaching",
        f"You have spoken {pct}% of the last {total} words",
        "Candidates reveal most when they do the talking. Hand the floor back with an open question.",
        priority="info",
        source="rule",
        timestamp=timestamp,
    )


def monologue_card(card_id: str, words: int, timestamp: str) -> dict[str, Any]:
    return copilot_card(
        card_id,
        "coaching",
        f"That was a long turn ({words} words)",
        "Check the candidate followed: ask them to restate the task in their own words before they start.",
        priority="info",
        source="rule",
        timestamp=timestamp,
    )


# ── Time steward and stuck countdown ───────────────────────────────────────

# (alert key, share of the question's time budget that triggers it); each fires once per question.
PACE_THRESHOLDS: tuple[tuple[str, float], ...] = (("over", 1.0), ("far_over", 1.5))
STUCK_QUIET_S = 60.0  # candidate silence (with code not moving) before the stuck countdown fires


def pace_alert(elapsed_s: float, budget_s: float, sent: list[str]) -> str | None:
    """The highest pace alert now due for this question that has not been sent yet, or None."""
    if budget_s <= 0:
        return None
    due = None
    for key, ratio in PACE_THRESHOLDS:
        if elapsed_s >= budget_s * ratio and key not in sent:
            due = key
    return due


def pace_card(
    card_id: str,
    key: str,
    elapsed_s: float,
    budget_s: float,
    remaining_questions: int,
    remaining_minutes: float,
    question_id: str,
    timestamp: str,
) -> dict[str, Any]:
    over_min = max(1, round((elapsed_s - budget_s) / 60))
    budget_min = max(1, round(budget_s / 60))
    plan_left = (
        f"{remaining_questions} question{'s' if remaining_questions != 1 else ''} and "
        f"{round(remaining_minutes)} min of plan left."
        if remaining_questions > 0
        else "This is the last planned question."
    )
    return copilot_card(
        card_id,
        "pace",
        f"{over_min} min over this question's {budget_min} min budget",
        f"{plan_left} Wrap up with what you have, or ask the short variant.",
        priority="urgent" if key == "far_over" else "suggest",
        source="rule",
        question_id=question_id,
        timestamp=timestamp,
    )


def stuck_card(card_id: str, quiet_s: float, question_id: str, timestamp: str) -> dict[str, Any]:
    return copilot_card(
        card_id,
        "stuck",
        f"Quiet for {round(quiet_s)} s and the code is not moving",
        "A hint is being drafted (Smart Hints). Or ask them to talk through what they are trying now.",
        priority="suggest",
        source="rule",
        question_id=question_id,
        timestamp=timestamp,
    )
