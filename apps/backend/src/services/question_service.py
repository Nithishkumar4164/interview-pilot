"""Question generation during the interview.

Nothing is planned up front. When the interviewer wants the next question they
pick a category, take one of the suggested topics (or type their own), and
generate a question with its hints, approaches and pitfalls. A draft that won't
work is regenerated; the rejected drafts are passed back so the next one differs.
Only the question the interviewer uses is logged (src/models/question.py).
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from src.llm.interface import AnalysisContext, LLMConfig, TaskType
from src.schemas.session import InterviewLevel
from src.services.calibration_service import get_calibration_profile
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from src.llm.failover import FailoverManager

logger = get_logger("question_service")

# The categories the interviewer can ask in; the same names as a session's focus areas.
QUESTION_CATEGORIES = ("coding", "dsa", "debugging", "system_design", "api_design", "behavioral", "aptitude")

# Categories whose questions must come with a 3-level hint ladder.
HINTED_CATEGORIES = ("coding", "dsa", "debugging", "system_design", "api_design", "aptitude")

TOPIC_SUGGESTION_COUNT = 5

QUESTION_GENERATION_SYSTEM_PROMPT = """You are an expert technical interviewer. Write the next question of a live interview with a software engineering candidate.

You will be given:
- level: the interview level (intern, sde1, sde2, senior, staff, principal)
- category: the kind of question to write (coding, dsa, debugging, system_design, api_design, behavioral, aptitude)
- topic: what the question must test, chosen by the interviewer (may be empty: then pick a topic that fits the category, level and resume)
- difficulty_range: the 1-5 difficulty suited to the level
- focus_areas: the interview's focus areas
- resume_context: the candidate's resume (may be empty)
- time_budget_minutes: the time left in the interview; the question must fit well inside it
- asked_questions: questions already asked in this interview. Do not ask any of them again, in any wording or framing.
- rejected_drafts: questions the interviewer already rejected for this slot. Write a clearly different question, not a rewording.
- interviewer_feedback: what the interviewer wants changed (may be empty)

Return a JSON object:
{
  "text": "The question as the interviewer will ask it",
  "category": "<the category given>",
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
    {"level": 1, "text": "Nudge: a Socratic question that does not reveal the approach"},
    {"level": 2, "text": "Guide: points to the concept or data structure"},
    {"level": 3, "text": "Direct: stops just short of the full answer"}
  ],
  "alternates": ["a follow-up or variant question"]
}

For coding/dsa/debugging questions, always provide both a brute_force_approach and at least one optimal_approaches entry with complexity analysis, key steps, and an optional code_snippet.
For system_design/api_design questions, provide optimal_approaches describing key design decisions.
For behavioral/aptitude questions, brute_force_approach and optimal_approaches may be null.

Aptitude questions test quantitative reasoning, estimation, probability or logic: the candidate works the answer out during the interview. Never ask about the candidate's past work in an aptitude question; that is a behavioral question.

The question must be appropriate for the level, specific and unambiguous, and come with hints at all 3 levels.
"""

TOPIC_SUGGESTION_SYSTEM_PROMPT = f"""You suggest topics for the next question of a live technical interview. The interviewer picks one, and a question is then written to test it.

You will be given the interview level, the category of the next question, the interview's focus areas, resume context (may be empty), and the questions already asked.

Suggest {TOPIC_SUGGESTION_COUNT} topics. Each has:
- concept: a few words naming the core concept it tests (e.g. "deduplication and idempotency", "rate limiting", "capacity estimation").
- topic: one sentence naming the problem the question would pose.

Rules:
- Fit the category: coding/dsa/debugging = an algorithmic, implementation or debugging problem; system_design/api_design = a system or API to design; aptitude = quantitative reasoning, estimation, probability or logic, worked out during the interview; behavioral = a past situation.
- No two suggestions may share a concept, and none may share a concept with an asked question. A concept is the same whether a question detects it, prevents it, implements it or estimates it.
- Where the resume gives material, ground some suggestions in the candidate's projects and stack; keep at least two independent of the resume.

Return JSON:
{{
  "topics": [
    {{"concept": "<a few words>", "topic": "<one sentence>"}}
  ]
}}
"""

HINT_PRECOMPUTE_SYSTEM_PROMPT = """You are an expert technical interviewer. Generate 3-level progressive hints for an interview question.

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

# One question with its approaches took 1,783-2,807 output tokens on claude-haiku-5-5
# (2026-10-19); only generated tokens are billed.
QUESTION_GENERATION_MAX_TOKENS = 8000

# Topic lists took 385-982 output tokens on claude-haiku-5-5 (2026-10-19).
TOPIC_SUGGESTION_MAX_TOKENS = 4000


@dataclass
class QuestionData:
    """A generated question, in the shape the live dashboard and analysis pipeline use."""

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

    def to_dict(self) -> dict:
        return asdict(self)


class QuestionGenerationError(Exception):
    """The model's answer held no usable question."""


@dataclass
class InterviewContext:
    """What generation needs to know about the session."""

    level: InterviewLevel
    focus_areas: list[str]
    resume_data: dict | None
    minutes_left: int
    asked_questions: list[str]


def _resume_context(resume_data: dict | None) -> dict:
    # The raw text duplicates the structured fields and costs tokens.
    return {k: v for k, v in (resume_data or {}).items() if k != "_raw_text"}


class QuestionService:
    def __init__(self, llm: "FailoverManager") -> None:
        self.llm = llm

    async def suggest_topics(self, ctx: InterviewContext, category: str) -> list[dict]:
        """Topics for the next question in ``category``, each {"concept", "topic"}.

        An unusable answer yields an empty list: the interviewer can still type a topic.
        """
        context = AnalysisContext(
            system_prompt=TOPIC_SUGGESTION_SYSTEM_PROMPT,
            user_content=json.dumps(
                {
                    "level": ctx.level.value,
                    "category": category,
                    "focus_areas": ctx.focus_areas,
                    "resume_context": _resume_context(ctx.resume_data),
                    "asked_questions": ctx.asked_questions,
                }
            ),
            task_type=TaskType.QUESTION_TOPICS,
        )
        # Thinking roughly doubled the wait on topic lists without better lists.
        config = LLMConfig(
            max_tokens=TOPIC_SUGGESTION_MAX_TOKENS, temperature=0.8, response_format="json", thinking=False
        )
        result = await self.llm.analyze(context, config)

        topics: list[dict] = []
        if result.parsed and isinstance(result.parsed.get("topics"), list):
            for entry in result.parsed["topics"]:
                if isinstance(entry, dict) and isinstance(entry.get("topic"), str) and entry["topic"].strip():
                    concept = entry.get("concept")
                    topics.append(
                        {
                            "concept": concept.strip() if isinstance(concept, str) else "",
                            "topic": entry["topic"].strip(),
                        }
                    )
        if not topics:
            logger.warning("topic_suggestion_empty", category=category, truncated=result.truncated)
        return topics[:TOPIC_SUGGESTION_COUNT]

    async def generate_question(
        self,
        ctx: InterviewContext,
        category: str,
        topic: str | None,
        rejected_drafts: list[str],
        feedback: str | None,
    ) -> QuestionData:
        """One question for ``category`` testing ``topic``, with hints, approaches and pitfalls."""
        difficulty_range = get_calibration_profile(ctx.level).difficulty_range
        context = AnalysisContext(
            system_prompt=QUESTION_GENERATION_SYSTEM_PROMPT,
            user_content=json.dumps(
                {
                    "level": ctx.level.value,
                    "category": category,
                    "topic": topic or "",
                    "difficulty_range": list(difficulty_range),
                    "focus_areas": ctx.focus_areas,
                    "resume_context": _resume_context(ctx.resume_data),
                    "time_budget_minutes": ctx.minutes_left,
                    "asked_questions": ctx.asked_questions,
                    "rejected_drafts": rejected_drafts,
                    "interviewer_feedback": feedback or "",
                }
            ),
            task_type=TaskType.QUESTION_GENERATION,
        )
        config = LLMConfig(
            max_tokens=QUESTION_GENERATION_MAX_TOKENS, temperature=0.7, response_format="json", thinking=False
        )
        result = await self.llm.analyze(context, config)

        q = result.parsed
        # Tolerate the older {"questions": [...]} shape.
        if isinstance(q, dict) and isinstance(q.get("questions"), list) and q["questions"]:
            q = q["questions"][0]
        if not isinstance(q, dict) or not isinstance(q.get("text"), str) or not q["text"].strip():
            logger.error("question_generation_unusable", category=category, truncated=result.truncated)
            raise QuestionGenerationError(
                "The question was cut off before it was complete" if result.truncated else "No question came back"
            )

        question = QuestionData(
            id=str(uuid.uuid4()),
            text=q["text"].strip(),
            category=category,
            difficulty=int(q.get("difficulty") or difficulty_range[0]),
            time_minutes=int(q.get("time_minutes") or 15),
            tags=q.get("tags") or [],
            hints=[h for h in (q.get("hints") or []) if isinstance(h, dict) and "level" in h and "text" in h],
            brute_force_approach=q.get("brute_force_approach"),
            optimal_approaches=q.get("optimal_approaches"),
            common_pitfalls=q.get("common_pitfalls"),
            alternates=q.get("alternates") or [],
        )
        if category in HINTED_CATEGORIES and len(question.hints) < 3:
            question.hints = await self.generate_hints(question)
        logger.info("question_generated", category=category, topic=topic, rejected=len(rejected_drafts))
        return question

    async def generate_hints(self, question: QuestionData) -> list[dict]:
        """3-level hints for a question that came back without a full ladder."""
        context = AnalysisContext(
            system_prompt=HINT_PRECOMPUTE_SYSTEM_PROMPT,
            user_content=json.dumps({"question": question.text, "optimal_approaches": question.optimal_approaches}),
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
