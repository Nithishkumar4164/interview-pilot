import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# A Google Meet meeting code, e.g. abc-defg-hij
_MEET_URL = re.compile(r"^(?:https://)?meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:[/?#].*)?$")


def normalize_meeting_url(raw: str | None) -> str | None:
    """The canonical https://meet.google.com/<code> link, or None for a blank value.

    Raises ValueError for anything that isn't a Google Meet meeting link.
    """
    if raw is None or not raw.strip():
        return None
    match = _MEET_URL.match(raw.strip().lower())
    if not match:
        raise ValueError("meeting_url must be a Google Meet link like https://meet.google.com/abc-defg-hij")
    return f"https://meet.google.com/{match.group(1)}"


def blank_to_none(raw: str | None) -> str | None:
    """Strip a free-text field; an empty one is stored as None."""
    if raw is None:
        return None
    return raw.strip() or None


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
    candidate_phone: str | None = None
    candidate_role: str | None = None
    resume_data: dict | None = None
    meeting_url: str | None = None

    _normalize_meeting_url = field_validator("meeting_url")(normalize_meeting_url)
    _strip_candidate_fields = field_validator("candidate_name", "candidate_email", "candidate_phone", "candidate_role")(
        blank_to_none
    )


class UpdateMeetingUrlRequest(BaseModel):
    meeting_url: str | None = None

    _normalize_meeting_url = field_validator("meeting_url")(normalize_meeting_url)


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
