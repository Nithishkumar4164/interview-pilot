from pydantic import BaseModel


class QuestionHintSchema(BaseModel):
    level: int
    text: str


class ApproachPatternSchema(BaseModel):
    name: str
    data_structures: list[str]
    expected_complexity: dict[str, str]
    key_steps: list[str]


class PlannedQuestionSchema(BaseModel):
    id: str
    text: str
    category: str
    difficulty: int
    time_minutes: int
    alternates: list[str]
    hints: list[QuestionHintSchema]
    optimal_approaches: list[ApproachPatternSchema] | None = None
    common_pitfalls: list[str] | None = None


class PlanPhaseSchema(BaseModel):
    phase: str
    start_minute: int
    duration_minutes: int
    questions: list[PlannedQuestionSchema]


class InterviewPlanSchema(BaseModel):
    id: str
    session_id: str
    version: int
    is_active: bool
    phases: list[PlanPhaseSchema]
    total_minutes: int
    created_at: str

    model_config = {"from_attributes": True}


class CreatePlanRequest(BaseModel):
    session_id: str


class SwapQuestionRequest(BaseModel):
    phase_index: int
    question_index: int
    alternate_index: int


class ReorderQuestionsRequest(BaseModel):
    phase_index: int
    new_order: list[int]
