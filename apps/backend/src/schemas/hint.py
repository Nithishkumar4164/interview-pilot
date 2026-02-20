from enum import Enum
from typing import Literal

from pydantic import BaseModel


class HintResponseQuality(str, Enum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class HintEventSchema(BaseModel):
    id: str
    session_id: str
    question_id: str
    level: Literal[1, 2, 3]
    text: str
    displayed_at: str
    interviewer_used: bool = False
    candidate_response_quality: HintResponseQuality = HintResponseQuality.NONE
    time_to_respond_ms: int | None = None

    model_config = {"from_attributes": True}


class HintResponseMetricsSchema(BaseModel):
    total_hints_given: int
    average_response_time_ms: float | None
    incorporation_rate: float  # 0-1
    level_distribution: dict[int, int]
    independence_trajectory: str  # improving | stable | declining


class TriggerHintRequest(BaseModel):
    question_id: str


class AcknowledgeHintRequest(BaseModel):
    interviewer_used: bool = True
    candidate_response_quality: HintResponseQuality = HintResponseQuality.NONE
