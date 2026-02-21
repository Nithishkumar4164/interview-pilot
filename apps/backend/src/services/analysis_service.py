"""Real-time utterance analysis service.

On every final utterance, assembles context from the memory hierarchy
and runs a structured analysis via the LLM provider.

Output: EnhancedAnalysisResult with 6-dimension insight evaluation
and backward-compatible top-level fields.
"""

import json
from datetime import UTC, datetime

from src.llm.failover import FailoverManager
from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.schemas.analysis import (
    DimensionScore,
    EnhancedAnalysisResult,
    InsightDimensions,
    RelevanceDimension,
    BehavioralSignalDimension,
    ContextualConsistencyDimension,
    HintReceptionDimension,
    DepthCompletenessDimension,
    CommunicationQualityDimension,
)
from src.services.memory_service import MemoryService
from src.utils.logging import get_logger
from src.utils.token_budget import BUDGET_RESPONSE, BUDGET_SYSTEM_PROMPT

logger = get_logger("analysis_service")

ENHANCED_ANALYSIS_SYSTEM_PROMPT = """You are a real-time interview analysis assistant helping an interviewer evaluate a software engineering candidate.

Analyze the candidate's most recent response in the context of the interview so far.

IMPORTANT: Always evaluate the candidate's response against the CURRENT question
provided in the context. The current question may change during the interview.
Ignore prior question context in the interview summary — only the explicitly
marked "Current Question" section reflects the active question.

Respond in JSON with this exact structure:
{
  "answer_quality": <1-5 integer>,
  "on_track": <true/false>,
  "insight": "<1-2 sentence observation about the candidate's response>",
  "red_flags": ["<list of concerns, empty if none>"],
  "suggestion_for_interviewer": "<1 actionable suggestion for the interviewer>",
  "hint_signal": "<none|nudge|guide|direct>",
  "dimensions": {
    "relevance": {
      "score": "<excellent|good|adequate|weak|poor>",
      "on_topic": <true/false>,
      "justification": "<brief reasoning>"
    },
    "behavioral_signal": {
      "score": "<excellent|good|adequate|weak|poor>",
      "signals_detected": ["<positive signals like structured_thinking, ownership>"],
      "flags": ["<concerns like vagueness, deflection>"],
      "justification": "<brief reasoning>"
    },
    "contextual_consistency": {
      "score": "<excellent|good|adequate|weak|poor>",
      "consistent": <true/false>,
      "contradictions": ["<any contradictions with prior statements>"],
      "justification": "<brief reasoning>"
    },
    HINT_RECEPTION_PLACEHOLDER
    "depth_completeness": {
      "score": "<excellent|good|adequate|weak|poor>",
      "key_points_covered": ["<concepts addressed>"],
      "key_points_missed": ["<important gaps>"],
      "justification": "<brief reasoning>"
    },
    "communication_quality": {
      "score": "<excellent|good|adequate|weak|poor>",
      "clarity": "<high|medium|low>",
      "structure": "<high|medium|low>",
      "confidence": "<high|medium|low>",
      "justification": "<brief reasoning>"
    }
  }
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

CODE_RATING_SYSTEM_PROMPT = """You are a code evaluation assistant for a live technical interview.

You are given the interview question, the expected approaches (brute force and optimal),
known common pitfalls, and the candidate's submitted code or pseudocode.

Evaluate the candidate's code against the known solutions and pitfalls.

Respond in JSON with this exact structure:
{
  "approach_match": "<optimal|brute_force|partial_optimal|novel|unclear>",
  "approach_name": "<name of the matched approach or 'N/A'>",
  "correctness": "<correct|mostly_correct|partially_correct|incorrect>",
  "correctness_issues": ["<list of correctness issues, empty if none>"],
  "complexity_actual": {"time": "<big-O>", "space": "<big-O>"},
  "complexity_expected": {"time": "<big-O>", "space": "<big-O>"},
  "complexity_match": <true/false>,
  "pitfalls_hit": ["<pitfalls from the known list that the code falls into>"],
  "pitfalls_avoided": ["<pitfalls from the known list that the code avoids>"],
  "strengths": ["<positive aspects of the code>"],
  "improvement_areas": ["<specific suggestions for improvement>"],
  "overall_score": <1-5 integer>,
  "summary": "<2-3 sentence overall assessment>"
}

Score scale:
  1 = Incorrect or fundamentally flawed
  2 = Partially correct but major issues
  3 = Adequate — works but suboptimal or has notable gaps
  4 = Good — solid solution with minor issues
  5 = Excellent — optimal or near-optimal, clean and correct"""

HINT_RECEPTION_BLOCK = """"hint_reception": {
      "score": "<excellent|good|adequate|weak|poor>",
      "acted_on_hint": <true/false>,
      "incorporation_level": "<full|partial|none>",
      "justification": "<how well they used the hint>"
    },"""


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
        self._analysis_history: list[EnhancedAnalysisResult] = []
        self._current_question: str = ""
        self._active_hint: str = ""
        self._question_plan: dict | None = None

    def set_current_question(self, question_text: str) -> None:
        """Update the current question context."""
        self._current_question = question_text

    def set_active_hint(self, hint_text: str) -> None:
        """Update the active hint context."""
        self._active_hint = hint_text

    def set_question_plan(self, question_data: dict) -> None:
        """Store the full planned question for richer analysis context."""
        self._question_plan = question_data

    def _build_system_prompt(self) -> str:
        """Build the system prompt, conditionally including hint_reception."""
        if self._active_hint:
            return ENHANCED_ANALYSIS_SYSTEM_PROMPT.replace(
                "HINT_RECEPTION_PLACEHOLDER", HINT_RECEPTION_BLOCK,
            )
        return ENHANCED_ANALYSIS_SYSTEM_PROMPT.replace(
            "    HINT_RECEPTION_PLACEHOLDER\n", "",
        )

    def _build_question_plan_context(self) -> str:
        """Build expected answer outline from the question plan."""
        if not self._question_plan:
            return ""

        parts = []
        qp = self._question_plan

        approaches = qp.get("optimal_approaches") or qp.get("optimal_approach")
        if approaches:
            if isinstance(approaches, list):
                parts.append("Optimal approaches: " + "; ".join(str(a) for a in approaches))
            else:
                parts.append(f"Optimal approach: {approaches}")

        pitfalls = qp.get("common_pitfalls")
        if pitfalls:
            if isinstance(pitfalls, list):
                parts.append("Common pitfalls: " + "; ".join(str(p) for p in pitfalls))
            else:
                parts.append(f"Common pitfalls: {pitfalls}")

        brute = qp.get("brute_force_approach")
        if brute:
            parts.append(f"Brute-force approach: {brute}")

        if not parts:
            return ""

        return "\n\n## Expected Answer Outline\n" + "\n".join(parts)

    async def analyze_utterance(
        self,
        speaker: str,
        text: str,
    ) -> EnhancedAnalysisResult:
        """Analyze a candidate utterance in context.

        Assembles the full context from memory tiers and runs LLM analysis.
        """
        # Assemble context from memory hierarchy
        context_text = self._memory.assemble_context(
            question_text=self._current_question,
            hint_text=self._active_hint,
        )

        # Append question plan context if available
        plan_context = self._build_question_plan_context()

        user_content = (
            f"{context_text}"
            f"{plan_context}\n\n"
            f"## Latest Utterance\n"
            f"[{speaker}]: {text}"
        )

        try:
            result = await self._failover.analyze(
                AnalysisContext(
                    system_prompt=self._build_system_prompt(),
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
            analysis = EnhancedAnalysisResult(
                insight=f"Analysis unavailable: {e}",
                answer_quality=0,
                timestamp=datetime.now(UTC).isoformat(),
            )

        if not analysis.timestamp:
            analysis.timestamp = datetime.now(UTC).isoformat()

        self._analysis_history.append(analysis)
        return analysis

    def _parse_analysis(
        self, content: str, parsed: dict | None
    ) -> EnhancedAnalysisResult:
        """Parse the LLM response into a structured EnhancedAnalysisResult."""
        data = parsed
        if not data:
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return EnhancedAnalysisResult(
                    insight=content[:200],
                    timestamp=datetime.now(UTC).isoformat(),
                )

        # Parse dimensions with fallback
        dimensions = self._parse_dimensions(data.get("dimensions"))

        return EnhancedAnalysisResult(
            answer_quality=min(5, max(1, int(data.get("answer_quality", 3)))),
            on_track=bool(data.get("on_track", True)),
            insight=str(data.get("insight", "")),
            red_flags=list(data.get("red_flags", [])),
            suggestion_for_interviewer=str(data.get("suggestion_for_interviewer", "")),
            hint_signal=str(data.get("hint_signal", "none")),
            dimensions=dimensions,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def _parse_dimensions(self, raw: dict | None) -> InsightDimensions | None:
        """Parse dimensions from LLM response with graceful degradation."""
        if not raw or not isinstance(raw, dict):
            return None

        try:
            return InsightDimensions.model_validate(raw)
        except Exception:
            # Attempt fallback construction from partial data
            return self._build_fallback_dimensions(raw)

    def _build_fallback_dimensions(self, raw: dict) -> InsightDimensions | None:
        """Construct dimensions from partial LLM data."""
        try:
            def _score(d: dict | None) -> DimensionScore:
                if not d or not isinstance(d, dict):
                    return DimensionScore.ADEQUATE
                s = str(d.get("score", "adequate")).lower()
                try:
                    return DimensionScore(s)
                except ValueError:
                    return DimensionScore.ADEQUATE

            def _str(d: dict | None, key: str, default: str = "") -> str:
                if not d or not isinstance(d, dict):
                    return default
                return str(d.get(key, default))

            def _bool(d: dict | None, key: str, default: bool = True) -> bool:
                if not d or not isinstance(d, dict):
                    return default
                return bool(d.get(key, default))

            def _list(d: dict | None, key: str) -> list[str]:
                if not d or not isinstance(d, dict):
                    return []
                val = d.get(key, [])
                return list(val) if isinstance(val, list) else []

            def _literal(d: dict | None, key: str, options: tuple, default: str) -> str:
                if not d or not isinstance(d, dict):
                    return default
                val = str(d.get(key, default)).lower()
                return val if val in options else default

            rel = raw.get("relevance")
            beh = raw.get("behavioral_signal")
            con = raw.get("contextual_consistency")
            dep = raw.get("depth_completeness")
            comm = raw.get("communication_quality")
            hint_r = raw.get("hint_reception")

            relevance = RelevanceDimension(
                score=_score(rel),
                on_topic=_bool(rel, "on_topic"),
                justification=_str(rel, "justification", "N/A"),
            )
            behavioral = BehavioralSignalDimension(
                score=_score(beh),
                signals_detected=_list(beh, "signals_detected"),
                flags=_list(beh, "flags"),
                justification=_str(beh, "justification", "N/A"),
            )
            consistency = ContextualConsistencyDimension(
                score=_score(con),
                consistent=_bool(con, "consistent"),
                contradictions=_list(con, "contradictions"),
                justification=_str(con, "justification", "N/A"),
            )
            depth = DepthCompletenessDimension(
                score=_score(dep),
                key_points_covered=_list(dep, "key_points_covered"),
                key_points_missed=_list(dep, "key_points_missed"),
                justification=_str(dep, "justification", "N/A"),
            )
            communication = CommunicationQualityDimension(
                score=_score(comm),
                clarity=_literal(comm, "clarity", ("high", "medium", "low"), "medium"),
                structure=_literal(comm, "structure", ("high", "medium", "low"), "medium"),
                confidence=_literal(comm, "confidence", ("high", "medium", "low"), "medium"),
                justification=_str(comm, "justification", "N/A"),
            )

            hint_reception = None
            if hint_r and isinstance(hint_r, dict):
                hint_reception = HintReceptionDimension(
                    score=_score(hint_r),
                    acted_on_hint=_bool(hint_r, "acted_on_hint", False),
                    incorporation_level=_literal(
                        hint_r, "incorporation_level",
                        ("full", "partial", "none"), "none",
                    ),
                    justification=_str(hint_r, "justification", "N/A"),
                )

            return InsightDimensions(
                relevance=relevance,
                behavioral_signal=behavioral,
                contextual_consistency=consistency,
                hint_reception=hint_reception,
                depth_completeness=depth,
                communication_quality=communication,
            )
        except Exception as e:
            logger.warning(
                "fallback_dimensions_failed",
                session_id=self.session_id,
                error=str(e),
            )
            return None

    async def rate_candidate_code(self, code: str, language: str = "unknown") -> dict:
        """Rate candidate code against the question plan's known solutions and pitfalls."""
        # Build user content from question plan context
        parts = []

        if self._current_question:
            parts.append(f"## Interview Question\n{self._current_question}")

        if self._question_plan:
            qp = self._question_plan
            brute = qp.get("brute_force_approach")
            if brute:
                if isinstance(brute, dict):
                    parts.append(f"## Brute Force Approach\nName: {brute.get('name', 'N/A')}\nComplexity: {brute.get('expected_complexity', {})}\nSteps: {brute.get('key_steps', [])}")
                else:
                    parts.append(f"## Brute Force Approach\n{brute}")

            approaches = qp.get("optimal_approaches") or qp.get("optimal_approach")
            if approaches:
                if isinstance(approaches, list):
                    for i, a in enumerate(approaches, 1):
                        if isinstance(a, dict):
                            parts.append(f"## Optimal Approach {i}\nName: {a.get('name', 'N/A')}\nComplexity: {a.get('expected_complexity', {})}\nSteps: {a.get('key_steps', [])}")
                        else:
                            parts.append(f"## Optimal Approach {i}\n{a}")
                else:
                    parts.append(f"## Optimal Approach\n{approaches}")

            pitfalls = qp.get("common_pitfalls")
            if pitfalls:
                if isinstance(pitfalls, list):
                    parts.append("## Common Pitfalls\n" + "\n".join(f"- {p}" for p in pitfalls))
                else:
                    parts.append(f"## Common Pitfalls\n{pitfalls}")

        parts.append(f"## Candidate's Code (language: {language})\n```{language}\n{code}\n```")

        user_content = "\n\n".join(parts)

        try:
            result = await self._failover.analyze(
                AnalysisContext(
                    system_prompt=CODE_RATING_SYSTEM_PROMPT,
                    user_content=user_content,
                    task_type=TaskType.CODE_RATING,
                ),
                LLMConfig(
                    max_tokens=800,
                    temperature=0.2,
                    response_format="json",
                ),
                session_id=self.session_id,
            )

            data = result.parsed
            if not data:
                try:
                    data = json.loads(result.content)
                except json.JSONDecodeError:
                    data = {}

            return {
                "approach_match": data.get("approach_match", "unclear"),
                "approach_name": data.get("approach_name", "N/A"),
                "correctness": data.get("correctness", "incorrect"),
                "correctness_issues": data.get("correctness_issues", []),
                "complexity_actual": data.get("complexity_actual", {}),
                "complexity_expected": data.get("complexity_expected", {}),
                "complexity_match": data.get("complexity_match", False),
                "pitfalls_hit": data.get("pitfalls_hit", []),
                "pitfalls_avoided": data.get("pitfalls_avoided", []),
                "strengths": data.get("strengths", []),
                "improvement_areas": data.get("improvement_areas", []),
                "overall_score": min(5, max(1, int(data.get("overall_score", 3)))),
                "summary": data.get("summary", ""),
                "latency_ms": result.latency_ms,
                "provider": result.provider,
            }

        except Exception as e:
            logger.error(
                "code_rating_failed",
                session_id=self.session_id,
                error=str(e),
            )
            return {
                "error": str(e),
                "overall_score": 0,
                "summary": f"Rating unavailable: {e}",
            }

    def get_recent_analyses(self, count: int = 5) -> list[EnhancedAnalysisResult]:
        """Get the most recent analysis results."""
        return self._analysis_history[-count:]

    def get_average_quality(self) -> float:
        """Get the running average answer quality score."""
        scored = [a for a in self._analysis_history if a.answer_quality > 0 and a.on_track]
        if not scored:
            return 0.0
        return sum(a.answer_quality for a in scored) / len(scored)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "analysis_count": len(self._analysis_history),
            "average_quality": self.get_average_quality(),
            "current_question": self._current_question,
            "recent_analyses": [
                a.model_dump() for a in self._analysis_history[-5:]
            ],
        }
