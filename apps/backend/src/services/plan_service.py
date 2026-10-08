"""Plan generation service.

Orchestrates: calibration → question selection → LLM question generation →
plan structuring with time-boxed phases and per-question time allocation.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.schemas.session import InterviewLevel

if TYPE_CHECKING:
    from src.llm.failover import FailoverManager
from src.services.calibration_service import (
    CalibrationProfile,
    PhaseAllocation,
)
from src.utils.logging import get_logger

logger = get_logger("plan_service")


# ── System prompt for question generation ─────────────────────────────────

QUESTION_GENERATION_SYSTEM_PROMPT = """You are an expert technical interviewer. Generate interview questions for a software engineering candidate.

You will be given:
- The interview level (intern, sde1, sde2, senior, staff, principal)
- Focus areas specified by the interviewer
- Resume context (if available)
- Category of question to generate (coding, system_design, behavioral, aptitude, dsa, debugging, api_design)
- Difficulty range (1-5 scale)

Return a JSON object with the following structure:
{
  "questions": [
    {
      "text": "The question text",
      "category": "coding|system_design|behavioral|aptitude",
      "difficulty": 1-5,
      "time_minutes": estimated minutes,
      "tags": ["tag1", "tag2"],
      "brute_force_approach": {
        "name": "Brute Force",
        "data_structures": ["ds1"],
        "expected_complexity": {"time": "O(n^2)", "space": "O(1)"},
        "key_steps": ["step1", "step2"],
        "code_snippet": "optional pseudocode or short code"
      },
      "optimal_approaches": [
        {
          "name": "Optimal: ...",
          "data_structures": ["ds1"],
          "expected_complexity": {"time": "O(n)", "space": "O(1)"},
          "key_steps": ["step1", "step2"],
          "code_snippet": "optional pseudocode or short code"
        }
      ],
      "common_pitfalls": ["pitfall1"],
      "hints": [
        {"level": 1, "text": "Nudge hint"},
        {"level": 2, "text": "Guide hint"},
        {"level": 3, "text": "Direct hint"}
      ],
      "alternates": ["alternate question 1"]
    }
  ]
}

For coding/dsa/debugging questions, always provide both a brute_force_approach and at least one optimal_approaches entry with complexity analysis, key steps, and an optional code_snippet.
For system_design/api_design questions, provide optimal_approaches describing key design decisions.
For behavioral/aptitude questions, brute_force_approach and optimal_approaches may be omitted.

Generate questions that are:
- Appropriate for the specified level
- Relevant to the focus areas and resume (if provided)
- Progressive in difficulty within each category
- Well-structured with clear hints at all 3 levels
"""

HINT_PRECOMPUTE_SYSTEM_PROMPT = """You are an expert technical interviewer. Generate 3-level progressive hints for a coding question.

For each question, generate hints at three levels:
- Level 1 (Nudge): A gentle directional hint, Socratic question style. Does NOT reveal the approach.
- Level 2 (Guide): Points to a specific concept or data structure. Narrows the solution space.
- Level 3 (Direct): Near-explicit guidance that stops just short of giving the full answer.

Return JSON:
{
  "hints": [
    {"level": 1, "text": "..."},
    {"level": 2, "text": "..."},
    {"level": 3, "text": "..."}
  ]
}
"""


@dataclass
class QuestionData:
    """A question selected or generated for the interview plan."""

    id: str
    text: str
    category: str
    difficulty: int
    time_minutes: int
    tags: list[str] = field(default_factory=list)
    hints: list[dict] = field(default_factory=list)
    brute_force_approach: dict | None = None
    optimal_approaches: list[dict] | None = None
    common_pitfalls: list[str] | None = None
    alternates: list[str] = field(default_factory=list)
    source: str = "bank"  # "bank" or "generated"


@dataclass
class PlanPhaseData:
    """A phase in the interview plan."""

    phase: str
    start_minute: int
    duration_minutes: int
    questions: list[QuestionData]


@dataclass
class GeneratedPlan:
    """The complete generated interview plan."""

    session_id: str
    phases: list[PlanPhaseData]
    total_minutes: int
    intro_minutes: int
    wrap_up_minutes: int
    buffer_minutes: int
    level: str
    focus_areas: list[str]


# Full question-generation answers took 2,136-2,540 output tokens on claude-sonnet-5
# (2026-10-08); only generated tokens are billed.
QUESTION_GENERATION_MAX_TOKENS = 8000


class PlanService:
    """Generates and manages interview plans."""

    def __init__(self, llm: "FailoverManager") -> None:
        self.llm = llm

    async def generate_plan(
        self,
        session_id: str,
        level: InterviewLevel,
        duration_minutes: int,
        focus_areas: list[str],
        resume_data: dict | None = None,
        bank_questions: list[dict] | None = None,
    ) -> GeneratedPlan:
        """Generate a full interview plan.

        Args:
            session_id: The session this plan belongs to.
            level: Interview level for calibration.
            duration_minutes: Total interview duration.
            focus_areas: Interviewer's focus areas.
            resume_data: Structured resume data (from M1) if available.
            bank_questions: Pre-seeded questions from QuestionBank, grouped by category.
        """
        from src.graphs.plan_graph import run_plan_graph

        return await run_plan_graph(
            self,
            session_id=session_id,
            level=level,
            duration_minutes=duration_minutes,
            focus_areas=focus_areas,
            resume_data=resume_data,
            bank_questions=bank_questions,
        )

    async def select_questions_for_phase(
        self,
        phase_alloc: PhaseAllocation,
        phase_minutes: int,
        profile: CalibrationProfile,
        focus_areas: list[str],
        resume_data: dict | None,
        bank_questions: list[dict] | None,
    ) -> list[QuestionData]:
        """Select questions for a phase from bank or generate via LLM."""
        questions: list[QuestionData] = []
        remaining_minutes = phase_minutes

        # Try to pick from the question bank first
        if bank_questions:
            bank_matches = self._filter_bank_questions(
                bank_questions,
                categories=phase_alloc.categories,
                difficulty_range=profile.difficulty_range,
                focus_tags=focus_areas + profile.preferred_tags,
            )
            for bq in bank_matches:
                if len(questions) >= phase_alloc.max_questions:
                    break
                q_time = min(bq.get("expected_time_min", 15), remaining_minutes)
                if q_time <= 0:
                    break
                questions.append(
                    QuestionData(
                        id=str(bq.get("id", uuid.uuid4())),
                        text=bq["text"],
                        category=bq["category"],
                        difficulty=bq["difficulty"],
                        time_minutes=q_time,
                        tags=bq.get("tags", []),
                        hints=bq.get("hints", []),
                        brute_force_approach=bq.get("brute_force_approach"),
                        optimal_approaches=bq.get("optimal_approaches"),
                        common_pitfalls=bq.get("common_pitfalls"),
                        alternates=bq.get("alternates", []),
                        source="bank",
                    )
                )
                remaining_minutes -= q_time

        # If we need more questions, generate them via LLM
        if len(questions) < phase_alloc.min_questions and remaining_minutes > 0:
            needed = phase_alloc.min_questions - len(questions)
            generated = await self._generate_questions(
                count=needed,
                categories=phase_alloc.categories,
                difficulty_range=profile.difficulty_range,
                focus_areas=focus_areas,
                level=profile.level.value,
                resume_data=resume_data,
                time_budget=remaining_minutes,
            )
            questions.extend(generated)

        # Rebalance time across questions in this phase
        questions = self._rebalance_time(questions, phase_minutes)

        return questions

    def _filter_bank_questions(
        self,
        bank_questions: list[dict],
        categories: list[str],
        difficulty_range: tuple[int, int],
        focus_tags: list[str],
    ) -> list[dict]:
        """Filter and rank bank questions by relevance."""
        matches = []
        focus_set = set(focus_tags)

        for q in bank_questions:
            if q["category"] not in categories:
                continue
            if not (difficulty_range[0] <= q["difficulty"] <= difficulty_range[1]):
                continue
            # Score by tag overlap
            tag_overlap = len(focus_set & set(q.get("tags", [])))
            matches.append((tag_overlap, q))

        # Sort by tag overlap (descending), then difficulty
        matches.sort(key=lambda x: (-x[0], x[1]["difficulty"]))
        return [m[1] for m in matches]

    async def _generate_questions(
        self,
        count: int,
        categories: list[str],
        difficulty_range: tuple[int, int],
        focus_areas: list[str],
        level: str,
        resume_data: dict | None,
        time_budget: int,
    ) -> list[QuestionData]:
        """Generate questions via LLM when bank doesn't have enough."""
        user_content = json.dumps(
            {
                "count": count,
                "categories": categories,
                "difficulty_range": list(difficulty_range),
                "focus_areas": focus_areas,
                "level": level,
                "resume_context": resume_data or {},
                "time_budget_minutes": time_budget,
            }
        )

        context = AnalysisContext(
            system_prompt=QUESTION_GENERATION_SYSTEM_PROMPT,
            user_content=user_content,
            task_type=TaskType.QUESTION_GENERATION,
        )
        config = LLMConfig(max_tokens=QUESTION_GENERATION_MAX_TOKENS, temperature=0.7, response_format="json")

        result = await self.llm.analyze(context, config)
        if result.truncated:
            # The JSON is incomplete: this phase gets no generated questions.
            logger.error("question_generation_truncated", categories=categories, max_tokens=config.max_tokens)

        questions: list[QuestionData] = []
        if result.parsed and "questions" in result.parsed:
            per_q_time = time_budget // max(count, 1)
            for q_data in result.parsed["questions"][:count]:
                questions.append(
                    QuestionData(
                        id=str(uuid.uuid4()),
                        text=q_data["text"],
                        category=q_data.get("category", categories[0]),
                        difficulty=q_data.get("difficulty", difficulty_range[0]),
                        time_minutes=q_data.get("time_minutes", per_q_time),
                        tags=q_data.get("tags", []),
                        hints=q_data.get("hints", []),
                        brute_force_approach=q_data.get("brute_force_approach"),
                        optimal_approaches=q_data.get("optimal_approaches"),
                        common_pitfalls=q_data.get("common_pitfalls"),
                        alternates=q_data.get("alternates", []),
                        source="generated",
                    )
                )

        return questions

    async def generate_hints(self, question: QuestionData) -> list[dict]:
        """Pre-generate 3-level hints for a coding question."""
        context = AnalysisContext(
            system_prompt=HINT_PRECOMPUTE_SYSTEM_PROMPT,
            user_content=json.dumps(
                {
                    "question": question.text,
                    "optimal_approaches": question.optimal_approaches,
                }
            ),
            task_type=TaskType.HINT_GENERATION,
        )
        config = LLMConfig(max_tokens=500, temperature=0.5, response_format="json")

        result = await self.llm.analyze(context, config)
        if result.parsed and "hints" in result.parsed:
            return result.parsed["hints"]

        # Fallback: generic hints
        return [
            {"level": 1, "text": "Think about the data structures that could help here."},
            {"level": 2, "text": "Consider the time/space complexity trade-offs."},
            {"level": 3, "text": "Try breaking the problem into smaller subproblems."},
        ]

    def _rebalance_time(
        self,
        questions: list[QuestionData],
        total_phase_minutes: int,
    ) -> list[QuestionData]:
        """Rebalance question time allocations to fit within the phase duration."""
        if not questions:
            return questions

        current_total = sum(q.time_minutes for q in questions)
        if current_total == 0:
            # Equal split
            per_q = total_phase_minutes // len(questions)
            for q in questions:
                q.time_minutes = per_q
            return questions

        # Scale proportionally
        scale = total_phase_minutes / current_total
        for q in questions:
            q.time_minutes = max(int(q.time_minutes * scale), 5)

        # Distribute remainder to the last question
        allocated = sum(q.time_minutes for q in questions)
        diff = total_phase_minutes - allocated
        if diff != 0 and questions:
            questions[-1].time_minutes += diff

        return questions

    # ── Plan editing operations ──────────────────────────────────────────────

    def swap_question(
        self,
        plan: GeneratedPlan,
        phase_index: int,
        question_index: int,
        alternate_index: int,
    ) -> GeneratedPlan:
        """Swap a question with one of its alternates."""
        if phase_index >= len(plan.phases):
            raise ValueError(f"Phase index {phase_index} out of range")

        phase = plan.phases[phase_index]
        if question_index >= len(phase.questions):
            raise ValueError(f"Question index {question_index} out of range")

        question = phase.questions[question_index]
        if not question.alternates or alternate_index >= len(question.alternates):
            raise ValueError(f"Alternate index {alternate_index} out of range")

        # Swap: current text becomes an alternate, alternate becomes the text
        old_text = question.text
        question.text = question.alternates[alternate_index]
        question.alternates[alternate_index] = old_text

        # Clear pre-computed hints (need regeneration for the new question)
        question.hints = []
        question.brute_force_approach = None
        question.optimal_approaches = None
        question.common_pitfalls = None

        return plan

    def reorder_questions(
        self,
        plan: GeneratedPlan,
        phase_index: int,
        new_order: list[int],
    ) -> GeneratedPlan:
        """Reorder questions within a phase."""
        if phase_index >= len(plan.phases):
            raise ValueError(f"Phase index {phase_index} out of range")

        phase = plan.phases[phase_index]
        if sorted(new_order) != list(range(len(phase.questions))):
            raise ValueError("new_order must be a permutation of question indices")

        phase.questions = [phase.questions[i] for i in new_order]
        return plan

    def remove_question(
        self,
        plan: GeneratedPlan,
        phase_index: int,
        question_index: int,
    ) -> GeneratedPlan:
        """Remove a question and rebalance time."""
        if phase_index >= len(plan.phases):
            raise ValueError(f"Phase index {phase_index} out of range")

        phase = plan.phases[phase_index]
        if question_index >= len(phase.questions):
            raise ValueError(f"Question index {question_index} out of range")

        phase.questions.pop(question_index)

        # Rebalance remaining questions
        if phase.questions:
            phase.questions = self._rebalance_time(phase.questions, phase.duration_minutes)

        return plan

    def add_question(
        self,
        plan: GeneratedPlan,
        phase_index: int,
        question: QuestionData,
    ) -> GeneratedPlan:
        """Add a question to a phase and rebalance time."""
        if phase_index >= len(plan.phases):
            raise ValueError(f"Phase index {phase_index} out of range")

        phase = plan.phases[phase_index]
        phase.questions.append(question)
        phase.questions = self._rebalance_time(phase.questions, phase.duration_minutes)

        return plan

    def plan_to_dict(self, plan: GeneratedPlan) -> dict:
        """Serialize a plan to a dict for storage in the DB (plan_data JSON field)."""
        return {
            "session_id": plan.session_id,
            "level": plan.level,
            "focus_areas": plan.focus_areas,
            "total_minutes": plan.total_minutes,
            "intro_minutes": plan.intro_minutes,
            "wrap_up_minutes": plan.wrap_up_minutes,
            "buffer_minutes": plan.buffer_minutes,
            "phases": [
                {
                    "phase": p.phase,
                    "start_minute": p.start_minute,
                    "duration_minutes": p.duration_minutes,
                    "questions": [
                        {
                            "id": q.id,
                            "text": q.text,
                            "category": q.category,
                            "difficulty": q.difficulty,
                            "time_minutes": q.time_minutes,
                            "tags": q.tags,
                            "hints": q.hints,
                            "brute_force_approach": q.brute_force_approach,
                            "optimal_approaches": q.optimal_approaches,
                            "common_pitfalls": q.common_pitfalls,
                            "alternates": q.alternates,
                            "source": q.source,
                        }
                        for q in p.questions
                    ],
                }
                for p in plan.phases
            ],
        }

    @staticmethod
    def plan_from_dict(data: dict) -> GeneratedPlan:
        """Deserialize a plan from a DB-stored dict."""
        phases = []
        for p_data in data.get("phases", []):
            questions = []
            for q_data in p_data.get("questions", []):
                questions.append(
                    QuestionData(
                        id=q_data["id"],
                        text=q_data["text"],
                        category=q_data["category"],
                        difficulty=q_data["difficulty"],
                        time_minutes=q_data["time_minutes"],
                        tags=q_data.get("tags", []),
                        hints=q_data.get("hints", []),
                        brute_force_approach=q_data.get("brute_force_approach"),
                        optimal_approaches=q_data.get("optimal_approaches"),
                        common_pitfalls=q_data.get("common_pitfalls"),
                        alternates=q_data.get("alternates", []),
                        source=q_data.get("source", "bank"),
                    )
                )
            phases.append(
                PlanPhaseData(
                    phase=p_data["phase"],
                    start_minute=p_data["start_minute"],
                    duration_minutes=p_data["duration_minutes"],
                    questions=questions,
                )
            )

        return GeneratedPlan(
            session_id=data["session_id"],
            phases=phases,
            total_minutes=data["total_minutes"],
            intro_minutes=data.get("intro_minutes", 3),
            wrap_up_minutes=data.get("wrap_up_minutes", 5),
            buffer_minutes=data.get("buffer_minutes", 0),
            level=data["level"],
            focus_areas=data.get("focus_areas", []),
        )
