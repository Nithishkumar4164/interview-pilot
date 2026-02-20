"""Plan API routes.

POST   /api/plans/create          — Generate an interview plan
GET    /api/plans/:id             — Get a plan
PATCH  /api/plans/:id             — Edit a plan (swap/reorder/remove/add)
POST   /api/plans/:id/regenerate  — Regenerate a specific question's hints
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db, failover_manager
from src.models.plan import InterviewPlan
from src.repositories.plan_repo import PlanRepository
from src.schemas.plan import (
    CreatePlanRequest,
    InterviewPlanSchema,
    ReorderQuestionsRequest,
    SwapQuestionRequest,
)
from src.schemas.session import InterviewLevel
from src.services.plan_service import PlanService, QuestionData

router = APIRouter(prefix="/plans", tags=["plans"])


class CreatePlanFullRequest(BaseModel):
    session_id: str
    level: InterviewLevel
    duration_minutes: int
    focus_areas: list[str]
    resume_data: dict | None = None


class RemoveQuestionRequest(BaseModel):
    phase_index: int
    question_index: int


class AddQuestionRequest(BaseModel):
    phase_index: int
    text: str
    category: str
    difficulty: int
    time_minutes: int
    tags: list[str] = []


class RegenerateHintsRequest(BaseModel):
    phase_index: int
    question_index: int


def _get_plan_service() -> PlanService:
    return PlanService(llm=failover_manager)


@router.post("/create", response_model=InterviewPlanSchema)
async def create_plan(
    request: CreatePlanFullRequest,
    db: AsyncSession = Depends(get_db),
) -> InterviewPlanSchema:
    """Generate a new interview plan for a session."""
    service = _get_plan_service()
    plan_repo = PlanRepository(db)

    # Generate the plan
    generated = await service.generate_plan(
        session_id=request.session_id,
        level=request.level,
        duration_minutes=request.duration_minutes,
        focus_areas=request.focus_areas,
        resume_data=request.resume_data,
    )

    # Deactivate existing plans for this session
    await plan_repo.deactivate_all(uuid.UUID(request.session_id))

    # Persist the new plan
    plan_data = service.plan_to_dict(generated)
    db_plan = InterviewPlan(
        session_id=uuid.UUID(request.session_id),
        plan_data=plan_data,
        version=1,
        is_active=True,
    )
    await plan_repo.create(db_plan)
    await db.commit()

    return InterviewPlanSchema(
        id=str(db_plan.id),
        session_id=request.session_id,
        version=db_plan.version,
        is_active=db_plan.is_active,
        phases=[
            {
                "phase": p["phase"],
                "start_minute": p["start_minute"],
                "duration_minutes": p["duration_minutes"],
                "questions": [
                    {
                        "id": q["id"],
                        "text": q["text"],
                        "category": q["category"],
                        "difficulty": q["difficulty"],
                        "time_minutes": q["time_minutes"],
                        "alternates": q.get("alternates", []),
                        "hints": q.get("hints", []),
                        "optimal_approaches": q.get("optimal_approaches"),
                        "common_pitfalls": q.get("common_pitfalls"),
                    }
                    for q in p["questions"]
                ],
            }
            for p in plan_data["phases"]
        ],
        total_minutes=generated.total_minutes,
        created_at=db_plan.created_at.isoformat() if db_plan.created_at else "",
    )


@router.get("/{plan_id}", response_model=InterviewPlanSchema)
async def get_plan(
    plan_id: str,
    db: AsyncSession = Depends(get_db),
) -> InterviewPlanSchema:
    """Get a plan by ID."""
    plan_repo = PlanRepository(db)
    db_plan = await plan_repo.get_by_id(uuid.UUID(plan_id))

    if db_plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found",
        )

    plan_data = db_plan.plan_data
    return InterviewPlanSchema(
        id=str(db_plan.id),
        session_id=str(db_plan.session_id),
        version=db_plan.version,
        is_active=db_plan.is_active,
        phases=[
            {
                "phase": p["phase"],
                "start_minute": p["start_minute"],
                "duration_minutes": p["duration_minutes"],
                "questions": [
                    {
                        "id": q["id"],
                        "text": q["text"],
                        "category": q["category"],
                        "difficulty": q["difficulty"],
                        "time_minutes": q["time_minutes"],
                        "alternates": q.get("alternates", []),
                        "hints": q.get("hints", []),
                        "optimal_approaches": q.get("optimal_approaches"),
                        "common_pitfalls": q.get("common_pitfalls"),
                    }
                    for q in p["questions"]
                ],
            }
            for p in plan_data.get("phases", [])
        ],
        total_minutes=plan_data.get("total_minutes", 0),
        created_at=db_plan.created_at.isoformat() if db_plan.created_at else "",
    )


@router.patch("/{plan_id}/swap", response_model=InterviewPlanSchema)
async def swap_question(
    plan_id: str,
    request: SwapQuestionRequest,
    db: AsyncSession = Depends(get_db),
) -> InterviewPlanSchema:
    """Swap a question with one of its alternates."""
    plan_repo = PlanRepository(db)
    db_plan = await plan_repo.get_by_id(uuid.UUID(plan_id))

    if db_plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    service = _get_plan_service()
    plan = PlanService.plan_from_dict(db_plan.plan_data)

    try:
        plan = service.swap_question(plan, request.phase_index, request.question_index, request.alternate_index)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    db_plan.plan_data = service.plan_to_dict(plan)
    await plan_repo.update(db_plan)
    await db.commit()

    return await get_plan(plan_id, db)


@router.patch("/{plan_id}/reorder", response_model=InterviewPlanSchema)
async def reorder_questions(
    plan_id: str,
    request: ReorderQuestionsRequest,
    db: AsyncSession = Depends(get_db),
) -> InterviewPlanSchema:
    """Reorder questions within a phase."""
    plan_repo = PlanRepository(db)
    db_plan = await plan_repo.get_by_id(uuid.UUID(plan_id))

    if db_plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    service = _get_plan_service()
    plan = PlanService.plan_from_dict(db_plan.plan_data)

    try:
        plan = service.reorder_questions(plan, request.phase_index, request.new_order)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    db_plan.plan_data = service.plan_to_dict(plan)
    await plan_repo.update(db_plan)
    await db.commit()

    return await get_plan(plan_id, db)


@router.patch("/{plan_id}/remove", response_model=InterviewPlanSchema)
async def remove_question(
    plan_id: str,
    request: RemoveQuestionRequest,
    db: AsyncSession = Depends(get_db),
) -> InterviewPlanSchema:
    """Remove a question from a phase."""
    plan_repo = PlanRepository(db)
    db_plan = await plan_repo.get_by_id(uuid.UUID(plan_id))

    if db_plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    service = _get_plan_service()
    plan = PlanService.plan_from_dict(db_plan.plan_data)

    try:
        plan = service.remove_question(plan, request.phase_index, request.question_index)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    db_plan.plan_data = service.plan_to_dict(plan)
    await plan_repo.update(db_plan)
    await db.commit()

    return await get_plan(plan_id, db)


@router.patch("/{plan_id}/add", response_model=InterviewPlanSchema)
async def add_question(
    plan_id: str,
    request: AddQuestionRequest,
    db: AsyncSession = Depends(get_db),
) -> InterviewPlanSchema:
    """Add a question to a phase."""
    plan_repo = PlanRepository(db)
    db_plan = await plan_repo.get_by_id(uuid.UUID(plan_id))

    if db_plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    service = _get_plan_service()
    plan = PlanService.plan_from_dict(db_plan.plan_data)

    new_q = QuestionData(
        id=str(uuid.uuid4()),
        text=request.text,
        category=request.category,
        difficulty=request.difficulty,
        time_minutes=request.time_minutes,
        tags=request.tags,
        source="manual",
    )

    try:
        plan = service.add_question(plan, request.phase_index, new_q)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    db_plan.plan_data = service.plan_to_dict(plan)
    await plan_repo.update(db_plan)
    await db.commit()

    return await get_plan(plan_id, db)


@router.post("/{plan_id}/regenerate-hints", response_model=InterviewPlanSchema)
async def regenerate_hints(
    plan_id: str,
    request: RegenerateHintsRequest,
    db: AsyncSession = Depends(get_db),
) -> InterviewPlanSchema:
    """Regenerate hints for a specific question."""
    plan_repo = PlanRepository(db)
    db_plan = await plan_repo.get_by_id(uuid.UUID(plan_id))

    if db_plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    service = _get_plan_service()
    plan = PlanService.plan_from_dict(db_plan.plan_data)

    if request.phase_index >= len(plan.phases):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phase index out of range")

    phase = plan.phases[request.phase_index]
    if request.question_index >= len(phase.questions):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question index out of range")

    question = phase.questions[request.question_index]
    question.hints = await service._generate_hints(question)

    db_plan.plan_data = service.plan_to_dict(plan)
    await plan_repo.update(db_plan)
    await db.commit()

    return await get_plan(plan_id, db)
