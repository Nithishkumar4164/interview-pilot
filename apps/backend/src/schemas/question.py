from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.schemas.session import blank_to_none

QuestionCategory = Literal["coding", "dsa", "debugging", "system_design", "api_design", "behavioral", "aptitude"]


class SuggestTopicsRequest(BaseModel):
    category: QuestionCategory


class GenerateQuestionRequest(BaseModel):
    category: QuestionCategory
    topic: str | None = Field(default=None, max_length=500)
    # Earlier drafts for this slot the interviewer rejected; the next one must differ.
    rejected_drafts: list[str] = Field(default_factory=list, max_length=20)
    feedback: str | None = Field(default=None, max_length=1000)

    _strip = field_validator("topic", "feedback")(blank_to_none)


class LogQuestionRequest(BaseModel):
    category: QuestionCategory
    topic: str | None = Field(default=None, max_length=500)
    # The generated question as the dashboard shows it (text, hints, approaches, pitfalls).
    question: dict

    _strip = field_validator("topic")(blank_to_none)

    @field_validator("question")
    @classmethod
    def _has_text(cls, question: dict) -> dict:
        text = question.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("question must have text")
        return question
