"""Real-time utterance analysis service.

On every final utterance, assembles context from the memory hierarchy
and runs a structured analysis via the LLM provider.

Output: answer_quality, on_track, insight, red_flags, suggestion, hint_signal
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.services.memory_service import MemoryService
from src.utils.logging import get_logger
from src.utils.token_budget import BUDGET_RESPONSE, BUDGET_SYSTEM_PROMPT

logger = get_logger("analysis_service")

ANALYSIS_SYSTEM_PROMPT = """You are a real-time interview analysis assistant helping an interviewer evaluate a software engineering candidate.

Analyze the candidate's most recent response in the context of the interview so far.

Respond in JSON with this exact structure:
{
  "answer_quality": <1-5 integer>,
  "on_track": <true/false>,
  "insight": "<1-2 sentence observation about the candidate's response>",
  "red_flags": ["<list of concerns, empty if none>"],
  "suggestion_for_interviewer": "<1 actionable suggestion for the interviewer>",
  "hint_signal": "<none|nudge|guide|direct — whether the candidate needs a hint>"
}

Quality scale:
  1 = Incorrect or completely off-track
  2 = Partially correct but significant gaps
  3 = Adequate — correct approach with some issues
  4 = Good — solid approach, minor gaps
  5 = Excellent — optimal approach, clear communication

hint_signal:
  none = Candidate is progressing well
  nudge = Candidate seems slightly stuck, a gentle nudge might help
  guide = Candidate is stuck, needs a concept-level hint
  direct = Candidate is very stuck, needs explicit direction"""


@dataclass
class AnalysisResult:
    """Structured result from a real-time analysis."""

    answer_quality: int = 3
    on_track: bool = True
    insight: str = ""
    red_flags: list[str] = field(default_factory=list)
    suggestion_for_interviewer: str = ""
    hint_signal: str = "none"
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    latency_ms: float = 0
    provider: str = ""

    def to_dict(self) -> dict:
        return {
            "answer_quality": self.answer_quality,
            "on_track": self.on_track,
            "insight": self.insight,
            "red_flags": self.red_flags,
            "suggestion_for_interviewer": self.suggestion_for_interviewer,
            "hint_signal": self.hint_signal,
            "timestamp": self.timestamp,
            "latency_ms": self.latency_ms,
            "provider": self.provider,
        }


class AnalysisService:
    """Runs real-time analysis on interview utterances.

    Usage:
        service = AnalysisService(session_id, failover_manager, memory_service)
        result = await service.analyze_utterance(speaker, text)
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
        self._analysis_history: list[AnalysisResult] = []
        self._current_question: str = ""
        self._active_hint: str = ""

    def set_current_question(self, question_text: str) -> None:
        """Update the current question context."""
        self._current_question = question_text

    def set_active_hint(self, hint_text: str) -> None:
        """Update the active hint context."""
        self._active_hint = hint_text

    async def analyze_utterance(
        self,
        speaker: str,
        text: str,
    ) -> AnalysisResult:
        """Analyze a candidate utterance in context.

        Assembles the full context from memory tiers and runs LLM analysis.
        """
        # Assemble context from memory hierarchy
        context_text = self._memory.assemble_context(
            question_text=self._current_question,
            hint_text=self._active_hint,
        )

        user_content = (
            f"{context_text}\n\n"
            f"## Latest Utterance\n"
            f"[{speaker}]: {text}"
        )

        try:
            result = await self._failover.analyze(
                AnalysisContext(
                    system_prompt=ANALYSIS_SYSTEM_PROMPT,
                    user_content=user_content,
                    task_type=TaskType.REAL_TIME_ANALYSIS,
                ),
                LLMConfig(
                    max_tokens=BUDGET_RESPONSE,
                    temperature=0.3,
                    response_format="json",
                ),
                session_id=self.session_id,
            )

            analysis = self._parse_analysis(result.content, result.parsed)
            analysis.latency_ms = result.latency_ms
            analysis.provider = result.provider

        except Exception as e:
            logger.error(
                "analysis_failed",
                session_id=self.session_id,
                error=str(e),
            )
            analysis = AnalysisResult(
                insight=f"Analysis unavailable: {e}",
                answer_quality=0,
            )

        self._analysis_history.append(analysis)
        return analysis

    def _parse_analysis(
        self, content: str, parsed: dict | None
    ) -> AnalysisResult:
        """Parse the LLM response into a structured AnalysisResult."""
        data = parsed
        if not data:
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return AnalysisResult(insight=content[:200])

        return AnalysisResult(
            answer_quality=min(5, max(1, int(data.get("answer_quality", 3)))),
            on_track=bool(data.get("on_track", True)),
            insight=str(data.get("insight", "")),
            red_flags=list(data.get("red_flags", [])),
            suggestion_for_interviewer=str(data.get("suggestion_for_interviewer", "")),
            hint_signal=str(data.get("hint_signal", "none")),
        )

    def get_recent_analyses(self, count: int = 5) -> list[AnalysisResult]:
        """Get the most recent analysis results."""
        return self._analysis_history[-count:]

    def get_average_quality(self) -> float:
        """Get the running average answer quality score."""
        scored = [a for a in self._analysis_history if a.answer_quality > 0]
        if not scored:
            return 0.0
        return sum(a.answer_quality for a in scored) / len(scored)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "analysis_count": len(self._analysis_history),
            "average_quality": self.get_average_quality(),
            "current_question": self._current_question,
            "recent_analyses": [a.to_dict() for a in self._analysis_history[-5:]],
        }
