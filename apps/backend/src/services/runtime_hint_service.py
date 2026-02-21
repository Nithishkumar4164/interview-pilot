"""Runtime hint service — LLM-generated contextual hints.

Generates interview hints dynamically based on the current question,
candidate transcript, and recent analysis insights. Supports
regeneration with reason-based adjustment.
"""

import json
import uuid
from datetime import UTC, datetime

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.schemas.analysis import EnhancedAnalysisResult
from src.schemas.hint import (
    RegenerationReason,
    RuntimeHintDirectness,
    RuntimeHintRequest,
    RuntimeHintResponse,
)
from src.services.memory_service import MemoryService
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


class RuntimeHintService:
    """Generates runtime LLM-powered hints for interview sessions.

    Usage:
        service = RuntimeHintService(session_id, failover_manager, memory_service)
        service.set_question(question_data)
        response = await service.generate_hint(request)
    """

    def __init__(
        self,
        session_id: str,
        failover_manager: FailoverManager,
        memory: MemoryService,
    ) -> None:
        self.session_id = session_id
        self._failover = failover_manager
        self._memory = memory
        self._current_question: dict | None = None
        self._recent_insights: list[EnhancedAnalysisResult] = []
        self._generation_history: list[RuntimeHintResponse] = []
        self._generation_count: dict[str, int] = {}  # question_id → count

    def set_question(self, question_data: dict) -> None:
        """Store the full planned question."""
        self._current_question = question_data

    def add_insight(self, result: EnhancedAnalysisResult) -> None:
        """Append to recent insights buffer (keeps last 5)."""
        self._recent_insights.append(result)
        self._recent_insights = self._recent_insights[-5:]

    async def generate_hint(
        self, request: RuntimeHintRequest,
    ) -> RuntimeHintResponse:
        """Generate a contextual hint for the current question."""
        start = datetime.now(UTC)
        question_id = request.question_id

        # Track generation count per question
        gen_count = self._generation_count.get(question_id, 0) + 1
        self._generation_count[question_id] = gen_count

        # Determine directness
        directness = self._resolve_directness(request)

        # Build user content
        user_content = self._assemble_context(request, directness)

        try:
            result = await self._failover.analyze(
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
                session_id=self.session_id,
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
                generation_number=gen_count,
                latency_ms=elapsed_ms,
                provider=result.provider,
            )

        except Exception as e:
            logger.error(
                "runtime_hint_generation_failed",
                session_id=self.session_id,
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
                generation_number=gen_count,
                latency_ms=elapsed_ms,
            )

        self._generation_history.append(response)

        logger.info(
            "runtime_hint_generated",
            session_id=self.session_id,
            question_id=question_id,
            directness=response.directness.value,
            generation_number=gen_count,
            is_regeneration=request.regenerate,
            latency_ms=response.latency_ms,
        )

        return response

    def _resolve_directness(
        self, request: RuntimeHintRequest,
    ) -> RuntimeHintDirectness:
        """Determine hint directness from request, insight, or default."""
        if request.preferred_directness:
            return request.preferred_directness

        # Derive from latest insight's hint_signal
        if self._recent_insights:
            signal = self._recent_insights[-1].hint_signal
            if signal == "direct":
                return RuntimeHintDirectness.DIRECT
            elif signal == "guide":
                return RuntimeHintDirectness.MODERATE
            elif signal == "nudge":
                return RuntimeHintDirectness.SUBTLE

        return RuntimeHintDirectness.MODERATE

    def _assemble_context(
        self,
        request: RuntimeHintRequest,
        directness: RuntimeHintDirectness,
    ) -> str:
        """Build the user content for the hint generation prompt."""
        parts: list[str] = []

        # Current question
        if self._current_question:
            q_text = self._current_question.get("text", "")
            if q_text:
                parts.append(f"## Current Question\n{q_text}")

            # Expected approaches
            approaches = (
                self._current_question.get("optimal_approaches")
                or self._current_question.get("optimal_approach")
            )
            if approaches:
                if isinstance(approaches, list):
                    parts.append(
                        "## Expected Approaches\n" + "\n".join(f"- {a}" for a in approaches)
                    )
                else:
                    parts.append(f"## Expected Approach\n{approaches}")

        # Candidate transcript from working memory
        working_mem = self._memory.get_working_memory_text()
        if working_mem:
            parts.append(f"## Recent Candidate Transcript\n{working_mem}")

        # Rolling summary
        if self._memory._rolling_summary:
            parts.append(f"## Interview Summary\n{self._memory._rolling_summary}")

        # Recent insights
        if self._recent_insights:
            insight_texts = []
            for ins in self._recent_insights[-3:]:
                insight_texts.append(
                    f"- Quality {ins.answer_quality}/5: {ins.insight}"
                )
            parts.append("## Recent Analysis\n" + "\n".join(insight_texts))

        # Directness instruction
        parts.append(f"## Directness Level\nGenerate a {directness.value} hint.")

        # Regeneration context
        if request.regenerate and self._generation_history:
            prev = self._generation_history[-1]
            reason_text = (
                request.regeneration_reason.value
                if request.regeneration_reason
                else "unspecified"
            )
            parts.append(
                f"## Regeneration Context\n"
                f"Previous hint was rejected (reason: {reason_text}):\n"
                f'"{prev.text}"\n'
                f"Generate a different hint that addresses this feedback."
            )

        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "generation_count": dict(self._generation_count),
            "total_hints": len(self._generation_history),
            "recent_insights_count": len(self._recent_insights),
        }
