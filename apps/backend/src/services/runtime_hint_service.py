"""Runtime hint service — LLM-generated contextual hints.

Generates interview hints dynamically based on the current question,
candidate transcript, and recent analysis insights. Supports
regeneration with reason-based adjustment.
"""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.schemas.analysis import EnhancedAnalysisResult
from src.schemas.hint import (
    RuntimeHintDirectness,
    RuntimeHintRequest,
    RuntimeHintResponse,
)
from src.utils.logging import get_logger
from src.utils.token_budget import BUDGET_RUNTIME_HINT

logger = get_logger("runtime_hint_service")

RUNTIME_HINT_SYSTEM_PROMPT = """You are an interview hint assistant. Generate a single contextual hint to help the interviewer guide the candidate.

The hint should be phrased as something the interviewer can say or rephrase for the candidate.

Respond in JSON:
{
  "hint_text": "<the hint text>",
  "directness": "<subtle|moderate|direct>",
  "rationale": "<why this hint is appropriate>"
}

Directness levels:
  subtle = A Socratic question or gentle nudge without revealing the answer
  moderate = Points toward a specific concept or technique
  direct = Explicitly states the approach or key insight"""


# ── Used by the LangGraph session pipeline ─────────────────────────────────


async def generate_runtime_hint(
    failover: FailoverManager,
    session_id: str,
    request: RuntimeHintRequest,
    directness: RuntimeHintDirectness,
    user_content: str,
    generation_number: int,
) -> RuntimeHintResponse:
    """Run the hint-generation LLM call. Never raises; a failure yields a placeholder hint."""
    start = datetime.now(UTC)
    question_id = request.question_id

    try:
        result = await failover.analyze(
            AnalysisContext(
                system_prompt=RUNTIME_HINT_SYSTEM_PROMPT,
                user_content=user_content,
                task_type=TaskType.HINT_GENERATION,
            ),
            LLMConfig(
                max_tokens=BUDGET_RUNTIME_HINT,
                temperature=0.5,
                response_format="json",
            ),
            session_id=session_id,
        )

        parsed = result.parsed or {}
        if not parsed:
            try:
                parsed = json.loads(result.content)
            except json.JSONDecodeError:
                parsed = {}

        hint_text = str(parsed.get("hint_text", result.content[:200]))
        resp_directness = parsed.get("directness", directness.value)

        try:
            resp_directness_enum = RuntimeHintDirectness(resp_directness)
        except ValueError:
            resp_directness_enum = directness

        elapsed_ms = (datetime.now(UTC) - start).total_seconds() * 1000

        response = RuntimeHintResponse(
            hint_id=str(uuid.uuid4()),
            question_id=question_id,
            text=hint_text,
            directness=resp_directness_enum,
            is_regeneration=request.regenerate,
            regeneration_reason=request.regeneration_reason,
            generation_number=generation_number,
            latency_ms=elapsed_ms,
            provider=result.provider,
        )

    except Exception as e:
        logger.error(
            "runtime_hint_generation_failed",
            session_id=session_id,
            error=str(e),
        )
        elapsed_ms = (datetime.now(UTC) - start).total_seconds() * 1000
        response = RuntimeHintResponse(
            hint_id=str(uuid.uuid4()),
            question_id=question_id,
            text=f"Hint generation failed: {e}",
            directness=directness,
            is_regeneration=request.regenerate,
            regeneration_reason=request.regeneration_reason,
            generation_number=generation_number,
            latency_ms=elapsed_ms,
        )

    return response


def resolve_directness(
    request: RuntimeHintRequest,
    recent_insights: Sequence[EnhancedAnalysisResult],
) -> RuntimeHintDirectness:
    """Determine hint directness from request, insight, or default."""
    if request.preferred_directness:
        return request.preferred_directness

    # Derive from latest insight's hint_signal
    if recent_insights:
        signal = recent_insights[-1].hint_signal
        if signal == "direct":
            return RuntimeHintDirectness.DIRECT
        if signal == "guide":
            return RuntimeHintDirectness.MODERATE
        if signal == "nudge":
            return RuntimeHintDirectness.SUBTLE

    return RuntimeHintDirectness.MODERATE


def assemble_hint_context(
    request: RuntimeHintRequest,
    directness: RuntimeHintDirectness,
    current_question: dict | None,
    working_memory_text: str,
    rolling_summary: str,
    recent_insights: Sequence[EnhancedAnalysisResult],
    previous_hint_text: str | None,
) -> str:
    """Build the user content for the hint generation prompt."""
    parts: list[str] = []

    # Current question
    if current_question:
        q_text = current_question.get("text", "")
        if q_text:
            parts.append(f"## Current Question\n{q_text}")

        # Expected approaches
        approaches = current_question.get("optimal_approaches") or current_question.get("optimal_approach")
        if approaches:
            if isinstance(approaches, list):
                parts.append("## Expected Approaches\n" + "\n".join(f"- {a}" for a in approaches))
            else:
                parts.append(f"## Expected Approach\n{approaches}")

    # Candidate transcript from working memory
    working_mem = working_memory_text
    if working_mem:
        parts.append(f"## Recent Candidate Transcript\n{working_mem}")

    # Rolling summary
    summary = rolling_summary
    if summary:
        parts.append(f"## Interview Summary\n{summary}")

    # Recent insights
    if recent_insights:
        insight_texts = []
        for ins in recent_insights[-3:]:
            insight_texts.append(f"- Quality {ins.answer_quality}/5: {ins.insight}")
        parts.append("## Recent Analysis\n" + "\n".join(insight_texts))

    # Directness instruction
    parts.append(f"## Directness Level\nGenerate a {directness.value} hint.")

    # Regeneration context
    if request.regenerate and previous_hint_text is not None:
        reason_text = request.regeneration_reason.value if request.regeneration_reason else "unspecified"
        parts.append(
            f"## Regeneration Context\n"
            f"Previous hint was rejected (reason: {reason_text}):\n"
            f'"{previous_hint_text}"\n'
            f"Generate a different hint that addresses this feedback."
        )

    return "\n\n".join(parts)
