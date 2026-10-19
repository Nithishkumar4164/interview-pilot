"""In-interview questions: topic suggestions, generation, and the questionnaire.

POST /sessions/{id}/questions/topics    suggested topics for a category
POST /sessions/{id}/questions/generate  a draft question (not stored)
POST /sessions/{id}/questions           log the draft the interviewer used
GET  /sessions/{id}/questions           the questionnaire, in order
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.authz import authorized_session
from src.api.sessions import _get_or_create_hint_service
from src.dependencies import failover_manager, get_db
from src.models.question import SessionQuestion
from src.models.session import InterviewSession
from src.schemas.question import (
    GenerateQuestionRequest,
    LogQuestionRequest,
    SuggestTopicsRequest,
)
from src.schemas.session import InterviewLevel, SessionStatus
from src.services.question_service import (
    InterviewContext,
    QuestionGenerationError,
    QuestionService,
)
from src.utils.logging import get_logger

logger = get_logger("api.questions")

router = APIRouter(prefix="/sessions/{session_id}/questions", tags=["questions"])

# Statuses a question can be asked in; generating ahead of going live is fine.
_ASKING_STATUSES = (SessionStatus.LIVE, SessionStatus.PAUSED)


def _get_question_service() -> QuestionService:
    return QuestionService(llm=failover_manager)


async def _asked(db: AsyncSession, session_id: uuid.UUID) -> list[SessionQuestion]:
    result = await db.execute(
        select(SessionQuestion).where(SessionQuestion.session_id == session_id).order_by(SessionQuestion.position)
    )
    return list(result.scalars())


def question_row_to_dict(row: SessionQuestion) -> dict:
    return {
        "id": str(row.id),
        "position": row.position,
        "category": row.category,
        "topic": row.topic,
        "asked_at": row.asked_at.isoformat(),
        "question": row.question,
    }


async def _interview_context(db: AsyncSession, db_session: InterviewSession) -> InterviewContext:
    if db_session.status == SessionStatus.ENDED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The interview has ended")
    minutes_left = db_session.duration_minutes
    if db_session.started_at:
        elapsed = (datetime.now(UTC) - db_session.started_at).total_seconds() / 60
        minutes_left = int(db_session.duration_minutes - elapsed)
    return InterviewContext(
        level=InterviewLevel(db_session.level),
        focus_areas=list(db_session.focus_areas or []),
        resume_data=db_session.resume_data,
        # Past the planned end the interviewer may still ask a short one.
        minutes_left=max(minutes_left, 5),
        asked_questions=[row.question.get("text", "") for row in await _asked(db, db_session.id)],
    )


@router.post("/topics")
async def suggest_topics(
    req: SuggestTopicsRequest,
    db: AsyncSession = Depends(get_db),
    db_session: InterviewSession = Depends(authorized_session),
) -> dict:
    ctx = await _interview_context(db, db_session)
    try:
        topics = await _get_question_service().suggest_topics(ctx, req.category)
    except Exception as e:
        logger.error("topic_suggestion_failed", session_id=str(db_session.id), error=str(e))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Couldn't suggest topics") from e
    return {"category": req.category, "topics": topics}


@router.post("/generate")
async def generate_question(
    req: GenerateQuestionRequest,
    db: AsyncSession = Depends(get_db),
    db_session: InterviewSession = Depends(authorized_session),
) -> dict:
    ctx = await _interview_context(db, db_session)
    try:
        question = await _get_question_service().generate_question(
            ctx, req.category, req.topic, req.rejected_drafts, req.feedback
        )
    except QuestionGenerationError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    except Exception as e:
        logger.error("question_generation_failed", session_id=str(db_session.id), error=str(e))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Couldn't generate a question") from e
    return {"category": req.category, "topic": req.topic, "question": question.to_dict()}


@router.post("")
async def log_question(
    req: LogQuestionRequest,
    db: AsyncSession = Depends(get_db),
    db_session: InterviewSession = Depends(authorized_session),
) -> dict:
    """Add the question the interviewer is about to ask to the questionnaire."""
    if db_session.status not in _ASKING_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Questions can only be logged while the interview is live, currently: {db_session.status}",
        )

    row_id = uuid.uuid4()
    position = (
        await db.execute(
            select(func.coalesce(func.max(SessionQuestion.position), -1)).where(
                SessionQuestion.session_id == db_session.id
            )
        )
    ).scalar_one() + 1
    question = {**req.question, "id": str(row_id), "category": req.category}
    row = SessionQuestion(
        id=row_id,
        session_id=db_session.id,
        position=position,
        category=req.category,
        topic=req.topic,
        question=question,
        asked_at=datetime.now(UTC),
    )
    db.add(row)
    await db.commit()

    # The hint ladder works on the active question's precomputed hints.
    hint_map = {h["level"]: h["text"] for h in question.get("hints") or [] if "level" in h and "text" in h}
    if hint_map:
        service = _get_or_create_hint_service(str(db_session.id))
        service.register_hints(str(row_id), hint_map)
        service.activate_question(str(row_id))

    logger.info("question_logged", session_id=str(db_session.id), position=position, category=req.category)
    return question_row_to_dict(row)


@router.get("")
async def list_questions(
    db: AsyncSession = Depends(get_db),
    db_session: InterviewSession = Depends(authorized_session),
) -> dict:
    return {"questions": [question_row_to_dict(row) for row in await _asked(db, db_session.id)]}
