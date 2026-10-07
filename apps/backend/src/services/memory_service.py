"""Hierarchical memory service (3-tier).

Manages interview context across three memory tiers:
  Tier 1 (Working Memory): Last 10 verbatim utterances
  Tier 2 (Rolling Summary): Compressed summary of all utterances (<400 words)
  Tier 3 (Fact Store): Structured facts extracted from conversation

Token budgets enforce context window limits per tier.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.utils.logging import get_logger
from src.utils.token_budget import (
    BUDGET_FACT_STORE,
    BUDGET_ROLLING_SUMMARY,
    BUDGET_WORKING_MEMORY,
    estimate_tokens,
    truncate_to_budget,
)

logger = get_logger("memory_service")

# Tier 1 settings
WORKING_MEMORY_SIZE = 10

# Fact provenance kept for this many recent utterances (contradiction self-exclusion)
CLAIM_PROVENANCE_SIZE = 20

# Tier 2 settings — compress after this many new utterances
SUMMARY_TRIGGER_COUNT = 20

SUMMARY_SYSTEM_PROMPT = """You are a concise interview summarizer. Given the current summary and a batch of new utterances, produce an updated summary that:
1. Preserves all factual claims and technical details from the previous summary
2. Integrates the new utterances chronologically
3. Stays under 400 words
4. Uses third-person ("The candidate said..." / "The interviewer asked...")
5. Highlights any claimed experience, technical positions, or notable moments

Return ONLY the updated summary text, no preamble."""

FACT_EXTRACTION_PROMPT = """Analyze this interview utterance and extract any factual claims into the following JSON structure. Only include categories with actual findings. Return valid JSON only.

{{
  "claimed_experience": ["list of specific claims about experience or skills"],
  "technical_positions": ["list of technical opinions or approach preferences"],
  "behavioral_signals": ["list of communication or behavioral observations"],
  "key_numbers": ["list of specific metrics, numbers, or quantified claims"]
}}

Utterance (by {speaker}): {text}"""


@dataclass
class Utterance:
    """A single utterance stored in working memory."""

    speaker: str
    text: str
    timestamp: str
    is_final: bool = True


@dataclass
class FactStore:
    """Tier 3: Structured facts extracted from the conversation."""

    claimed_experience: list[str] = field(default_factory=list)
    technical_positions: list[str] = field(default_factory=list)
    behavioral_signals: list[str] = field(default_factory=list)
    key_numbers: list[str] = field(default_factory=list)
    hint_interactions: list[dict] = field(default_factory=list)

    def merge(self, extracted: dict) -> list[str]:
        """Merge newly extracted facts, avoiding duplicates. Returns the items that were new."""
        added: list[str] = []
        for key in (
            "claimed_experience",
            "technical_positions",
            "behavioral_signals",
            "key_numbers",
        ):
            existing = getattr(self, key)
            for item in extracted.get(key, []):
                if item and item not in existing:
                    existing.append(item)
                    added.append(item)
        return added

    def to_text(self) -> str:
        """Render fact store as concise text for the LLM context."""
        parts = []
        if self.claimed_experience:
            parts.append("Experience: " + "; ".join(self.claimed_experience))
        if self.technical_positions:
            parts.append("Technical positions: " + "; ".join(self.technical_positions))
        if self.behavioral_signals:
            parts.append("Behavioral: " + "; ".join(self.behavioral_signals))
        if self.key_numbers:
            parts.append("Numbers: " + "; ".join(self.key_numbers))
        if self.hint_interactions:
            parts.append(f"Hint interactions: {len(self.hint_interactions)} recorded")
        return "\n".join(parts) if parts else "(No facts extracted yet)"

    def to_dict(self) -> dict:
        return {
            "claimed_experience": list(self.claimed_experience),
            "technical_positions": list(self.technical_positions),
            "behavioral_signals": list(self.behavioral_signals),
            "key_numbers": list(self.key_numbers),
            "hint_interactions": list(self.hint_interactions),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FactStore":
        return cls(
            claimed_experience=data.get("claimed_experience", []),
            technical_positions=data.get("technical_positions", []),
            behavioral_signals=data.get("behavioral_signals", []),
            key_numbers=data.get("key_numbers", []),
            hint_interactions=data.get("hint_interactions", []),
        )


# ── Used by the LangGraph session pipeline ─────────────────────────────────


def should_extract_facts(speaker: str, text: str, is_final: bool) -> bool:
    """Tier 3 holds the candidate's claims: substantial final candidate utterances only.

    Interviewer utterances are not mined; their statements are not the
    candidate's claims and must not be contradiction-checked against.
    """
    return speaker == "candidate" and is_final and len(text.split()) > 20


def format_utterances(utterances: Iterable[Utterance]) -> str:
    return "\n".join(f"[{u.speaker}]: {u.text}" for u in utterances)


def assemble_context(
    working_memory: Iterable[Utterance],
    rolling_summary: str,
    fact_store: FactStore,
    question_text: str = "",
    hint_text: str = "",
) -> str:
    """Assemble the full context for an LLM analysis call.

    Returns a formatted string combining all tiers within token budgets.
    """
    parts = []

    # Current question context (first for LLM primacy effect)
    if question_text:
        parts.append(f"## Current Question\n{question_text}")

    # Active hints
    if hint_text:
        parts.append(f"## Active Hints\n{hint_text}")

    # Tier 3: Fact Store (most compressed, highest signal)
    facts_text = truncate_to_budget(fact_store.to_text(), BUDGET_FACT_STORE)
    if facts_text:
        parts.append(f"## Key Facts\n{facts_text}")

    # Tier 2: Rolling Summary
    if rolling_summary:
        summary = truncate_to_budget(rolling_summary, BUDGET_ROLLING_SUMMARY)
        parts.append(f"## Interview Summary\n{summary}")

    # Tier 1: Recent Utterances
    recent_text = format_utterances(working_memory)
    if recent_text:
        recent_text = truncate_to_budget(recent_text, BUDGET_WORKING_MEMORY)
        parts.append(f"## Recent Conversation\n{recent_text}")

    return "\n\n".join(parts)


async def compress_summary(
    failover: FailoverManager,
    session_id: str,
    rolling_summary: str,
    utterances: Sequence[Utterance],
) -> str:
    """Fold ``utterances`` into the rolling summary. Raises if the LLM call fails."""
    user_content = (
        f"Current summary:\n{rolling_summary or '(empty)'}\n\nNew utterances:\n{format_utterances(utterances)}"
    )
    result = await failover.analyze(
        AnalysisContext(
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            user_content=user_content,
            task_type=TaskType.SUMMARY_COMPRESSION,
        ),
        LLMConfig(max_tokens=500, temperature=0.3),
        session_id=session_id,
    )
    logger.debug(
        "summary_compressed",
        session_id=session_id,
        summary_tokens=estimate_tokens(result.content),
    )
    return result.content


async def extract_facts(failover: FailoverManager, session_id: str, speaker: str, text: str) -> dict | None:
    """Extract structured facts from a substantial utterance. Never raises; None when nothing parsed."""
    prompt = FACT_EXTRACTION_PROMPT.format(speaker=speaker, text=text)
    try:
        result = await failover.analyze(
            AnalysisContext(
                system_prompt="Extract factual claims from the utterance. Return valid JSON only.",
                user_content=prompt,
                task_type=TaskType.REAL_TIME_ANALYSIS,
            ),
            LLMConfig(max_tokens=300, temperature=0.2, response_format="json"),
            session_id=session_id,
        )
    except Exception as e:
        logger.error(
            "fact_extraction_failed",
            session_id=session_id,
            error=str(e),
        )
        return None
    if result.parsed:
        return result.parsed
    # Try parsing content directly
    try:
        parsed = json.loads(result.content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
