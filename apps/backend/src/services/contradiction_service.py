"""Rule-based contradiction detection.

A fast, deterministic pass over negation pairs: a new statement against the
facts the candidate stated earlier. It runs on every analyzed answer. The
LLM judgment of whether a statement conflicts with earlier ones is part of the
copilot's claim check (``peer_interviewer.verify_claim``), which also records
what it finds as ``Contradiction``s.
"""

import hashlib
import re
from collections.abc import Set
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.services.memory_service import FactStore
from src.utils.logging import get_logger

logger = get_logger("contradiction_service")

# ── Rule-Based Patterns ───────────────────────────────────────────────

# Negation pairs: (positive pattern, negative pattern)
NEGATION_PAIRS = [
    (
        r"\b(?:I have|I've)\s+(?:experience|worked|used)\b",
        r"\b(?:I(?:'ve)? never|I haven't|I don't have experience)\b",
    ),
    (
        r"\b(?:I led|I managed|I was (?:the )?lead)\b",
        r"\b(?:I(?:'ve)? never (?:led|managed)|wasn't (?:the )?lead)\b",
    ),
    (
        r"\b(?:we used|our team used|I used)\b",
        r"\b(?:I(?:'ve)? never used|we didn't use|I'm not familiar with)\b",
    ),
    (
        r"\b(?:I know|I'm familiar|I understand)\b",
        r"\b(?:I don't know|I'm not familiar|I don't understand)\b",
    ),
    (
        r"\b(?:I built|I implemented|I created|I designed)\b",
        r"\b(?:I(?:'ve)? never built|I didn't implement|I didn't create)\b",
    ),
    (
        r"\b(\d+)\s*(?:years?|months?)\s+(?:of )?experience\b",
        r"\b(?:just started|new to|beginner|learning)\b",
    ),
]


@dataclass
class Contradiction:
    """A detected contradiction between statements."""

    claim: str
    new_statement: str
    explanation: str
    confidence: float
    suggested_probe: str
    detection_method: str  # "rule_based" or "llm"
    detected_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# ── Used by the LangGraph session pipeline ─────────────────────────────────


def pair_key(claim: str, new_statement: str) -> str:
    """Stable, compact key for a (claim, statement) pair that has been checked."""
    return hashlib.sha256(f"{claim}\x00{new_statement}".encode()).hexdigest()[:16]


def check_contradictions(
    new_statement: str,
    fact_store: FactStore,
    checked_pairs: Set[str],
    exclude_claims: Set[str] = frozenset(),
) -> tuple[list[Contradiction], set[str]]:
    """The rule-based pass: a new statement against the facts the candidate stated earlier.

    ``exclude_claims`` are facts extracted from the statement itself; a
    statement is never checked against its own claims. Pairs whose
    ``pair_key`` is in ``checked_pairs`` are skipped. Returns the newly
    detected contradictions and the keys of the pairs checked now.
    """
    new_contradictions: list[Contradiction] = []
    newly_checked: set[str] = set()
    for claim in known_claims(fact_store):
        if claim in exclude_claims:
            continue
        key = pair_key(claim, new_statement)
        if key in checked_pairs or key in newly_checked:
            continue
        newly_checked.add(key)
        found = rule_based_check(claim, new_statement)
        if found:
            new_contradictions.append(found)
    if new_contradictions:
        logger.info("contradictions_detected", count=len(new_contradictions))
    return new_contradictions, newly_checked


def known_claims(fact_store: FactStore) -> list[str]:
    """The earlier statements a new one can contradict: experience, technical positions, numbers."""
    return fact_store.claimed_experience + fact_store.technical_positions + fact_store.key_numbers


def rule_based_check(claim: str, new_statement: str) -> Contradiction | None:
    """Fast rule-based negation pair detection."""
    for pos_pattern, neg_pattern in NEGATION_PAIRS:
        claim_has_positive = re.search(pos_pattern, claim, re.IGNORECASE)
        new_has_negative = re.search(neg_pattern, new_statement, re.IGNORECASE)

        if claim_has_positive and new_has_negative:
            return Contradiction(
                claim=claim,
                new_statement=new_statement,
                explanation=f"Candidate previously said '{claim_has_positive.group()}' but now says '{new_has_negative.group()}'",
                confidence=0.8,
                suggested_probe="Could you clarify your experience level with this technology?",
                detection_method="rule_based",
            )

        # Check reverse: claim is negative, new is positive
        claim_has_negative = re.search(neg_pattern, claim, re.IGNORECASE)
        new_has_positive = re.search(pos_pattern, new_statement, re.IGNORECASE)

        if claim_has_negative and new_has_positive:
            return Contradiction(
                claim=claim,
                new_statement=new_statement,
                explanation=f"Candidate previously said '{claim_has_negative.group()}' but now says '{new_has_positive.group()}'",
                confidence=0.8,
                suggested_probe="Could you clarify your experience level with this technology?",
                detection_method="rule_based",
            )

    return None
