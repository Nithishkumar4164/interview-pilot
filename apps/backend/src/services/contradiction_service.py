"""Contradiction detection service.

Detects inconsistencies in candidate statements using two passes:
  Pass 1: Rule-based negation pair detection (fast, high precision)
  Pass 2: LLM confirmation for ambiguous cases (slower, higher recall)

Generates contradiction events with suggested probe questions.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
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

# Technology name extractor
TECH_PATTERN = re.compile(
    r"\b(?:Python|Java|JavaScript|TypeScript|Go|Rust|C\+\+|Ruby|PHP|Swift|Kotlin|"
    r"React|Angular|Vue|Node\.?js|Django|Flask|Spring|Rails|"
    r"PostgreSQL|MySQL|MongoDB|Redis|Elasticsearch|"
    r"AWS|GCP|Azure|Docker|Kubernetes|Terraform|"
    r"GraphQL|REST|gRPC|Kafka|RabbitMQ)\b",
    re.IGNORECASE,
)

LLM_CONTRADICTION_PROMPT = """You are analyzing an interview transcript for contradictions.

Previous statement by the candidate:
"{claim}"

New statement by the candidate:
"{new_statement}"

Are these statements contradictory? Consider:
1. Direct contradictions (said X, now says not X)
2. Temporal contradictions (said 5 years experience, but timeline doesn't add up)
3. Technical contradictions (claimed to lead a project, but doesn't know basic details)

Respond in JSON:
{{
  "is_contradiction": true/false,
  "confidence": 0.0-1.0,
  "explanation": "Brief explanation",
  "suggested_probe": "A question the interviewer could ask to clarify"
}}"""


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


class ContradictionService:
    """Detects contradictions in candidate statements.

    Usage:
        service = ContradictionService(session_id, failover_manager)
        contradictions = await service.check(new_text, fact_store)
    """

    def __init__(self, session_id: str, failover_manager: FailoverManager) -> None:
        self.session_id = session_id
        self._failover = failover_manager
        self._contradictions: list[Contradiction] = []
        self._checked_pairs: set[tuple[str, str]] = set()

    async def check(
        self,
        new_statement: str,
        fact_store: FactStore,
    ) -> list[Contradiction]:
        """Check a new statement against known facts for contradictions.

        Returns a list of newly detected contradictions.
        """
        new_contradictions: list[Contradiction] = []

        # Check against claimed experience
        all_claims = fact_store.claimed_experience + fact_store.technical_positions + fact_store.key_numbers

        for claim in all_claims:
            # Skip already-checked pairs
            pair_key = (claim, new_statement)
            if pair_key in self._checked_pairs:
                continue
            self._checked_pairs.add(pair_key)

            # Pass 1: Rule-based detection
            rule_result = self._rule_based_check(claim, new_statement)
            if rule_result:
                new_contradictions.append(rule_result)
                continue

            # Pass 2: LLM check for technology-related statements
            if self._has_tech_overlap(claim, new_statement):
                llm_result = await self._llm_check(claim, new_statement)
                if llm_result:
                    new_contradictions.append(llm_result)

        self._contradictions.extend(new_contradictions)

        if new_contradictions:
            logger.info(
                "contradictions_detected",
                session_id=self.session_id,
                count=len(new_contradictions),
            )

        return new_contradictions

    def _rule_based_check(self, claim: str, new_statement: str) -> Contradiction | None:
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

    def _has_tech_overlap(self, claim: str, new_statement: str) -> bool:
        """Check if two statements reference the same technology."""
        claim_techs = set(TECH_PATTERN.findall(claim.lower()))
        new_techs = set(TECH_PATTERN.findall(new_statement.lower()))
        return bool(claim_techs & new_techs)

    async def _llm_check(self, claim: str, new_statement: str) -> Contradiction | None:
        """LLM-based contradiction check for ambiguous cases."""
        prompt = LLM_CONTRADICTION_PROMPT.format(
            claim=claim,
            new_statement=new_statement,
        )
        try:
            result = await self._failover.analyze(
                AnalysisContext(
                    system_prompt="Detect contradictions in interview statements. Return JSON.",
                    user_content=prompt,
                    task_type=TaskType.REAL_TIME_ANALYSIS,
                ),
                LLMConfig(max_tokens=200, temperature=0.2, response_format="json"),
                session_id=self.session_id,
            )

            parsed = result.parsed
            if not parsed:
                try:
                    parsed = json.loads(result.content)
                except json.JSONDecodeError:
                    return None

            if parsed.get("is_contradiction") and parsed.get("confidence", 0) > 0.6:
                return Contradiction(
                    claim=claim,
                    new_statement=new_statement,
                    explanation=parsed.get("explanation", "LLM detected contradiction"),
                    confidence=parsed.get("confidence", 0.7),
                    suggested_probe=parsed.get("suggested_probe", "Could you elaborate?"),
                    detection_method="llm",
                )
        except Exception as e:
            logger.error(
                "llm_contradiction_check_failed",
                session_id=self.session_id,
                error=str(e),
            )

        return None

    @property
    def contradictions(self) -> list[Contradiction]:
        return list(self._contradictions)

    def to_dict(self) -> dict:
        return {
            "contradictions": [
                {
                    "claim": c.claim,
                    "new_statement": c.new_statement,
                    "explanation": c.explanation,
                    "confidence": c.confidence,
                    "suggested_probe": c.suggested_probe,
                    "detection_method": c.detection_method,
                    "detected_at": c.detected_at,
                }
                for c in self._contradictions
            ],
        }
