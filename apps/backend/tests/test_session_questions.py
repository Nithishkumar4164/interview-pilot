"""In-interview questions through the real endpoints and Postgres; only the model is a stand-in.

The interviewer's path: suggest topics for a category, generate a draft,
reject it and regenerate, use the second draft (logged to the questionnaire),
then reload the page (state) and end the interview (report inputs).
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import src.api.questions as questions_api
from src.api.sessions import _hint_services
from src.config import settings
from src.dependencies import get_db
from src.llm.interface import AnalysisContext, LLMConfig, StructuredOutput, TaskType
from src.main import app
from src.models.question import SessionQuestion
from src.models.session import InterviewSession
from src.models.user import User
from src.services.auth_service import AuthService
from src.services.question_service import QuestionService
from src.services.report_service import ReportService


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")

HINTS = [{"level": 1, "text": "Nudge"}, {"level": 2, "text": "Guide"}, {"level": 3, "text": "Direct"}]


class _Model:
    """Answers like the real provider and records each request it was sent."""

    def __init__(self) -> None:
        self.requests: list[tuple[TaskType, dict]] = []
        self.drafts = 0
        self.hintless = False

    async def analyze(self, context: AnalysisContext, config: LLMConfig) -> StructuredOutput:
        request = json.loads(context.user_content) if context.user_content.startswith("{") else {}
        self.requests.append((context.task_type, request))
        if context.task_type == TaskType.QUESTION_TOPICS:
            answer = {"topics": [{"concept": "hashing", "topic": "Two sum"}, {"concept": "graphs", "topic": "Islands"}]}
        elif context.task_type == TaskType.HINT_GENERATION:
            answer = {"hints": HINTS}
        else:
            self.drafts += 1
            answer = {
                "text": f"Draft {self.drafts} about {request['topic']}",
                "difficulty": 3,
                "time_minutes": 20,
                "hints": [] if self.hintless else HINTS,
                "optimal_approaches": [{"name": "Optimal: hash map", "key_steps": ["store complements"]}],
                "common_pitfalls": ["Using an element twice"],
            }
        return StructuredOutput(content=json.dumps(answer), parsed=answer, output_tokens=100)

    def of(self, task_type: TaskType) -> list[dict]:
        return [r for t, r in self.requests if t == task_type]


@pytest.fixture
async def world(monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", False)
    monkeypatch.setattr(settings, "environment", "production")
    model = _Model()
    monkeypatch.setattr(questions_api, "_get_question_service", lambda: QuestionService(llm=model))

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    owner = User(id=uuid.uuid4(), email=f"owner-{uuid.uuid4().hex[:8]}@q.test", name="owner", role="interviewer")
    session = InterviewSession(
        id=uuid.uuid4(),
        user_id=owner.id,
        status="live",
        level="sde2",
        duration_minutes=45,
        focus_areas=["coding", "system_design"],
        resume_data={"name": "Jane", "tech_stack": ["Go"], "_raw_text": "Jane ... the whole resume"},
        started_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    async with factory() as db:
        db.add(owner)
        await db.flush()
        db.add(session)
        await db.commit()

    yield {
        "sid": str(session.id),
        "token": AuthService(None).create_access_token(str(owner.id)),
        "model": model,
        "factory": factory,
    }
    app.dependency_overrides.pop(get_db, None)
    _hint_services.pop(str(session.id), None)
    async with factory() as db:
        await db.execute(delete(InterviewSession).where(InterviewSession.id == session.id))
        await db.execute(delete(User).where(User.id == owner.id))
        await db.commit()
    await engine.dispose()


async def _call(world, method: str, path: str, json_body: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers={"Authorization": f"Bearer {world['token']}"}, json=json_body)


async def test_generate_regenerate_use_and_recover(world) -> None:
    sid, model = world["sid"], world["model"]
    base = f"/api/sessions/{sid}/questions"

    topics = await _call(world, "POST", f"{base}/topics", {"category": "coding"})
    assert topics.status_code == 200
    assert topics.json()["topics"][0] == {"concept": "hashing", "topic": "Two sum"}
    [topic_request] = model.of(TaskType.QUESTION_TOPICS)
    assert topic_request["category"] == "coding"
    assert topic_request["level"] == "sde2"
    assert "_raw_text" not in topic_request["resume_context"]

    first = await _call(world, "POST", f"{base}/generate", {"category": "coding", "topic": "Two sum"})
    assert first.status_code == 200
    draft1 = first.json()["question"]
    assert draft1["text"] == "Draft 1 about Two sum"
    assert draft1["hints"] == HINTS
    assert draft1["category"] == "coding"

    # Regenerate: the rejected draft and the interviewer's note go back to the model.
    second = await _call(
        world,
        "POST",
        f"{base}/generate",
        {"category": "coding", "topic": "Two sum", "rejected_drafts": [draft1["text"]], "feedback": "harder"},
    )
    draft2 = second.json()["question"]
    regen_request = model.of(TaskType.QUESTION_GENERATION)[-1]
    assert regen_request["rejected_drafts"] == ["Draft 1 about Two sum"]
    assert regen_request["interviewer_feedback"] == "harder"
    assert regen_request["difficulty_range"] == [2, 4]
    assert 30 <= regen_request["time_budget_minutes"] <= 35  # 45 planned, 10 gone

    # Generating stores nothing; using a draft logs it.
    assert (await _call(world, "GET", base)).json() == {"questions": []}
    used = await _call(world, "POST", base, {"category": "coding", "topic": "Two sum", "question": draft2})
    assert used.status_code == 200
    logged = used.json()
    assert logged["position"] == 0
    assert logged["question"]["id"] == logged["id"]
    assert logged["question"]["text"] == "Draft 2 about Two sum"

    # The hint ladder now runs on the used question.
    hints = _hint_services[sid]
    assert hints._active_question_id == logged["id"]

    # The next generation must not repeat what was asked.
    await _call(world, "POST", f"{base}/generate", {"category": "system_design", "topic": None})
    assert model.of(TaskType.QUESTION_GENERATION)[-1]["asked_questions"] == ["Draft 2 about Two sum"]
    second_used = await _call(
        world,
        "POST",
        base,
        {"category": "system_design", "question": {**draft1, "text": "Design a URL shortener"}},
    )
    assert second_used.json()["position"] == 1

    # A page reload restores the questionnaire, in order.
    state = (await _call(world, "GET", f"/api/sessions/{sid}/state")).json()
    assert [q["question"]["text"] for q in state["questions"]] == ["Draft 2 about Two sum", "Design a URL shortener"]
    assert state["session"]["focus_areas"] == ["coding", "system_design"]
    assert state["plan"] is None


async def test_report_reads_the_questionnaire_with_time_per_question(world) -> None:
    sid = uuid.UUID(world["sid"])
    start = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    async with world["factory"]() as db:
        for i, (category, minutes) in enumerate([("coding", 0), ("coding", 12), ("behavioral", 30)]):
            qid = uuid.uuid4()
            db.add(
                SessionQuestion(
                    id=qid,
                    session_id=sid,
                    position=i,
                    category=category,
                    question={"id": str(qid), "text": f"Q{i}"},
                    asked_at=start + timedelta(minutes=minutes),
                )
            )
        await db.execute(
            update(InterviewSession).where(InterviewSession.id == sid).values(ended_at=start + timedelta(minutes=40))
        )
        await db.commit()

    async with world["factory"]() as db:
        report_input = await ReportService(None).load_inputs(str(sid), {}, db)

    assert [(p["phase"], [q["text"] for q in p["questions"]]) for p in report_input.plan_data["phases"]] == [
        ("coding", ["Q0", "Q1"]),
        ("behavioral", ["Q2"]),
    ]
    assert sorted(report_input.time_per_question.values()) == [10.0, 12.0, 18.0]


async def test_questions_without_hints_get_a_generated_ladder(world) -> None:
    world["model"].hintless = True
    resp = await _call(world, "POST", f"/api/sessions/{world['sid']}/questions/generate", {"category": "coding"})
    assert resp.json()["question"]["hints"] == HINTS
    assert len(world["model"].of(TaskType.HINT_GENERATION)) == 1


async def test_nothing_is_asked_after_the_interview_ends(world) -> None:
    async with world["factory"]() as db:
        await db.execute(
            update(InterviewSession).where(InterviewSession.id == uuid.UUID(world["sid"])).values(status="ended")
        )
        await db.commit()
    base = f"/api/sessions/{world['sid']}/questions"
    assert (await _call(world, "POST", f"{base}/generate", {"category": "coding"})).status_code == 409
    logged = await _call(world, "POST", base, {"category": "coding", "question": {"text": "q"}})
    assert logged.status_code == 409


async def test_unknown_category_and_empty_question_are_rejected(world) -> None:
    base = f"/api/sessions/{world['sid']}/questions"
    assert (await _call(world, "POST", f"{base}/generate", {"category": "cooking"})).status_code == 422
    assert (await _call(world, "POST", base, {"category": "coding", "question": {"text": " "}})).status_code == 422
