from enum import Enum

from pydantic import BaseModel, Field


class InterviewLevel(str, Enum):
    INTERN = "intern"
    SDE1 = "sde1"
    SDE2 = "sde2"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"


class SessionStatus(str, Enum):
    PLANNING = "planning"
    READY = "ready"
    LIVE = "live"
    PAUSED = "paused"
    ENDED = "ended"


class CreateSessionRequest(BaseModel):
    level: InterviewLevel
    duration_minutes: int = Field(ge=15, le=120)
    focus_areas: list[str]
    job_description: str | None = None
    custom_notes: str | None = None
    resume_reference_id: str | None = None
    candidate_name: str | None = None
    candidate_email: str | None = None
    resume_data: dict | None = None


class SessionResponse(BaseModel):
    id: str
    user_id: str
    status: SessionStatus
    level: InterviewLevel
    duration_minutes: int
    focus_areas: list[str]
    job_description: str | None = None
    plan_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}
