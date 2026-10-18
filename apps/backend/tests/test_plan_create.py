"""Plan generation through the real endpoint (POST /api/plans/create), real Postgres.

The model is a stand-in that behaves like the real provider: each answer has
a full length and, when max_tokens is lower, comes back cut off at the limit
with ``truncated`` set (as Anthropic does on stop_reason=max_tokens).
"""

import json
import uuid
from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest

from src.config import settings
from src.llm.interface import AnalysisContext, LLMConfig, StructuredOutput, TaskType

# Measured on 2026-10-08 with claude-sonnet-5 (SDE-1, 60 min, coding/api_design/dsa/aptitude,
# synthetic resume): full question-generation answers took 2,136-2,540 output tokens.
MEASURED_QUESTION_ANSWER_TOKENS = 2540
HINTS = [{"level": 1, "text": "Nudge"}, {"level": 2, "text": "Guide"}, {"level": 3, "text": "Direct"}]


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


class _ModelWithAnswersOfLength:
    def __init__(self, question_tokens: int) -> None:
        self.question_tokens = question_tokens

    async def analyze(self, context: AnalysisContext, config: LLMConfig) -> StructuredOutput:
        if context.task_type == TaskType.HINT_GENERATION:
            answer, tokens = {"hints": HINTS}, 200
        elif context.task_type == TaskType.PLAN_TOPICS:
            phases = json.loads(context.user_content)["phases"]
            answer, tokens = {"phases": [{"phase": p["phase"], "topic": f"{p['phase']} topic"} for p in phases]}, 150
        else:
            categories = json.loads(context.user_content)["categories"]
            answer = {"questions": [{"text": f"A {categories[0]} question", "category": categories[0]}]}
            tokens = self.question_tokens
        full = json.dumps(answer)
        if config.max_tokens >= tokens:
            return StructuredOutput(content=full, parsed=answer, output_tokens=tokens)
        cut = full[: len(full) * config.max_tokens // tokens]
        return StructuredOutput(content=cut, parsed=None, output_tokens=config.max_tokens, truncated=True)


@pytest.fixture
async def session_id() -> AsyncIterator[str]:
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from src.dependencies import get_db
    from src.main import app
    from src.models.plan import InterviewPlan
    from src.models.session import InterviewSession
    from src.services.access_control import DEV_USER_ID

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    sid = uuid.uuid4()
    async with factory() as db:
        db.add(
            InterviewSession(
                id=sid,
                user_id=DEV_USER_ID,
                status="planning",
                level="sde1",
                duration_minutes=60,
                focus_areas=["coding"],
            )
        )
        await db.commit()
    yield str(sid)
    app.dependency_overrides.pop(get_db, None)
    async with factory() as db:
        await db.execute(delete(InterviewPlan).where(InterviewPlan.session_id == sid))
        await db.execute(delete(InterviewSession).where(InterviewSession.id == sid))
        await db.commit()
    await engine.dispose()


async def test_every_phase_gets_questions_at_the_measured_answer_length(session_id: str, monkeypatch) -> None:
    """Before the fix the limit was 2,000 tokens: answers were cut off and phases came back empty."""
    from src.api import plans
    from src.main import app
    from src.services.plan_service import PlanService

    monkeypatch.setattr(
        plans,
        "_get_plan_service",
        lambda: PlanService(llm=_ModelWithAnswersOfLength(MEASURED_QUESTION_ANSWER_TOKENS)),  # type: ignore[arg-type]
    )
    request = {
        "session_id": session_id,
        "level": "sde1",
        "duration_minutes": 60,
        "focus_areas": ["coding", "api_design", "dsa", "aptitude"],
        "resume_data": {"name": "Synthetic Candidate", "tech_stack": ["Go"]},
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/plans/create", json=request)
    assert response.status_code == 200, response.text
    phases = response.json()["phases"]
    assert phases and all(p["questions"] for p in phases), [(p["phase"], len(p["questions"])) for p in phases]


class _ModelThatRecordsPrompts:
    """Answers topic planning (unless ``topics`` is None) and question generation, keeping every request."""

    def __init__(self, topics: dict[str, str] | None) -> None:
        self.topics = topics
        self.requests: list[tuple[TaskType, dict]] = []

    async def analyze(self, context: AnalysisContext, config: LLMConfig) -> StructuredOutput:
        request = json.loads(context.user_content)
        self.requests.append((context.task_type, request))
        if context.task_type == TaskType.PLAN_TOPICS:
            if self.topics is None:
                return StructuredOutput(content="not json", parsed=None)
            answer: dict = {"phases": [{"phase": p, "topic": t} for p, t in self.topics.items()]}
        elif context.task_type == TaskType.HINT_GENERATION:
            answer = {"hints": HINTS}
        else:
            answer = {"questions": [{"text": f"A {request['categories'][0]} question", "hints": HINTS}]}
        return StructuredOutput(content=json.dumps(answer), parsed=answer)

    def of(self, task_type: TaskType) -> list[dict]:
        return [r for t, r in self.requests if t == task_type]


async def _create_plan(session_id: str, model: _ModelThatRecordsPrompts, monkeypatch) -> list[dict]:
    from src.api import plans
    from src.main import app
    from src.services.plan_service import PlanService

    monkeypatch.setattr(plans, "_get_plan_service", lambda: PlanService(llm=model))  # type: ignore[arg-type]
    request = {
        "session_id": session_id,
        "level": "sde2",
        "duration_minutes": 60,
        "focus_areas": ["coding", "aptitude", "dsa", "api_design"],
        "resume_data": {"key_projects": [{"name": "CBDC payments", "technologies": ["Go", "Kafka"]}]},
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/plans/create", json=request)
    assert response.status_code == 200, response.text
    return response.json()["phases"]


async def test_each_phase_is_written_to_its_own_topic_and_told_the_others(session_id: str, monkeypatch) -> None:
    """Phases were generated in parallel with identical inputs, so all three could ask about the same thing."""
    topics = {
        "coding": "Merge overlapping time intervals",
        "aptitude": "Probability of a hash collision",
        "system_design": "A notification fan-out service",
    }
    model = _ModelThatRecordsPrompts(topics)

    phases = await _create_plan(session_id, model, monkeypatch)

    assert all(p["questions"] for p in phases)
    [topic_request] = model.of(TaskType.PLAN_TOPICS)
    assert topic_request["phases"] == [
        {"phase": "coding", "focus_areas": ["coding", "dsa"]},
        {"phase": "aptitude", "focus_areas": ["aptitude"]},
        {"phase": "system_design", "focus_areas": ["api_design"]},
    ]
    question_requests = {r["categories"][0]: r for r in model.of(TaskType.QUESTION_GENERATION)}
    assert set(question_requests) == set(topics)
    for phase, request in question_requests.items():
        assert request["assigned_topic"] == topics[phase]
        assert sorted(request["topics_covered_by_other_phases"]) == sorted(t for p, t in topics.items() if p != phase)
    assert question_requests["aptitude"]["focus_areas"] == ["aptitude"]
    assert question_requests["system_design"]["focus_areas"] == ["api_design"]


async def test_plan_is_still_generated_when_topic_planning_fails(session_id: str, monkeypatch) -> None:
    model = _ModelThatRecordsPrompts(topics=None)

    phases = await _create_plan(session_id, model, monkeypatch)

    assert [p["phase"] for p in phases] == ["coding", "aptitude", "system_design"]
    assert all(p["questions"] for p in phases)
    assert all("assigned_topic" not in r for r in model.of(TaskType.QUESTION_GENERATION))
