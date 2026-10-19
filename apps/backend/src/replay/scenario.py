"""Scenario envelope for replaying interview transcripts headlessly."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from src.schemas.hint import (
    HintResponseQuality,
    RegenerationReason,
    RuntimeHintDirectness,
)
from src.schemas.transcript import SpeakerRole


class ScenarioMeta(BaseModel):
    id: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class TranscriptEvent(BaseModel):
    type: Literal["transcript"] = "transcript"
    speaker_role: SpeakerRole
    text: str
    is_final: bool = True
    confidence: float = 0.95
    channel: str = "tab"
    delay_ms: int = 0


class QuestionChangeEvent(BaseModel):
    type: Literal["question_change"] = "question_change"
    question_text: str
    question_plan: dict = Field(default_factory=dict)
    question_index: int = 0
    remaining_questions: int = 0
    remaining_minutes: float = 0.0
    delay_ms: int = 0


class HintRequestEvent(BaseModel):
    type: Literal["hint_request"] = "hint_request"
    question_id: str
    regenerate: bool = False
    regeneration_reason: RegenerationReason | None = None
    preferred_directness: RuntimeHintDirectness | None = None
    delay_ms: int = 0


class HintAckEvent(BaseModel):
    type: Literal["hint_ack"] = "hint_ack"
    hint_id: str
    question_id: str
    quality: HintResponseQuality = HintResponseQuality.NONE
    delay_ms: int = 0


class ActiveHintEvent(BaseModel):
    """The interviewer revealed a hint (REST hint endpoint -> ``set_active_hint``)."""

    type: Literal["active_hint"] = "active_hint"
    text: str
    delay_ms: int = 0


class CodeUpdateEvent(BaseModel):
    """A live editor snapshot from the code WebSocket (``SessionPipeline.on_code_snapshot``)."""

    type: Literal["code_update"] = "code_update"
    code: str
    language: str = "python"
    source: str = "codemirror"
    delay_ms: int = 0


class CodeSnapshotEvent(BaseModel):
    """The interviewer asked for a rating of this code (``handle_code_rating``)."""

    type: Literal["code_snapshot"] = "code_snapshot"
    code: str
    language: str = "python"
    question_id: str = ""
    delay_ms: int = 0


class CopilotCardUsedEvent(BaseModel):
    """The interviewer acted on a copilot card (dashboard ``use_copilot_card`` -> ``handle_copilot_card_used``)."""

    type: Literal["copilot_card_used"] = "copilot_card_used"
    card_id: str
    kind: str = "probe"
    text: str = ""
    question_id: str = ""
    delay_ms: int = 0


class ScorecardNoteEvent(BaseModel):
    """The interviewer typed a scorecard note (``handle_scorecard_note``)."""

    type: Literal["scorecard_note"] = "scorecard_note"
    note_id: str
    criterion: str
    text: str
    question_id: str = ""
    delay_ms: int = 0


class CopilotQueryEvent(BaseModel):
    """The interviewer asked the copilot a question (``handle_copilot_query``)."""

    type: Literal["copilot_query"] = "copilot_query"
    query_id: str
    question: str
    delay_ms: int = 0


class TickEvent(BaseModel):
    """The peer interviewer's clock (``on_tick`` at the scenario time)."""

    type: Literal["tick"] = "tick"
    delay_ms: int = 0


ScenarioEvent = Annotated[
    TranscriptEvent
    | QuestionChangeEvent
    | HintRequestEvent
    | HintAckEvent
    | ActiveHintEvent
    | CodeUpdateEvent
    | CodeSnapshotEvent
    | CopilotCardUsedEvent
    | CopilotQueryEvent
    | ScorecardNoteEvent
    | TickEvent,
    Field(discriminator="type"),
]


class Scenario(BaseModel):
    meta: ScenarioMeta
    events: list[ScenarioEvent]


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    with path.open() as f:
        return Scenario.model_validate(json.load(f))
