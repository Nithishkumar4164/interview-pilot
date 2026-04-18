from enum import Enum
from typing import Literal

from pydantic import BaseModel


class Recommendation(str, Enum):
    STRONG_HIRE = "strong_hire"
    HIRE = "hire"
    LEAN_HIRE = "lean_hire"
    LEAN_NO_HIRE = "lean_no_hire"
    NO_HIRE = "no_hire"


class CriterionScoreSchema(BaseModel):
    criterion: str
    score: int  # 1-5
    evidence: list[str]
    trend: str


KeyMomentType = Literal[
    "breakthrough",
    "struggle",
    "hint",
    "contradiction",
    "code_milestone",
    "strong_answer",
    "weak_answer",
]


class KeyMomentSchema(BaseModel):
    timestamp: str
    title: str
    description: str
    sentiment: str
    impact_on_assessment: str
    type: KeyMomentType | None = None


class AssessmentSchema(BaseModel):
    recommendation: Recommendation
    confidence_level: float
    scores: list[CriterionScoreSchema]
    key_moments: list[KeyMomentSchema]
    strengths: list[str]
    areas_for_growth: list[str]
    level_comparison: str


class ReportResponse(BaseModel):
    id: str
    session_id: str
    assessment: AssessmentSchema
    hint_summary: dict
    generated_at: str
    is_final: bool

    model_config = {"from_attributes": True}


class AddNoteRequest(BaseModel):
    note_text: str
