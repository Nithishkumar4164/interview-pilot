from enum import Enum

from pydantic import BaseModel


class SpeakerRole(str, Enum):
    INTERVIEWER = "interviewer"
    CANDIDATE = "candidate"
    SECOND_INTERVIEWER = "second_interviewer"
    RECRUITER = "recruiter"
    UNKNOWN = "unknown"


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    confidence: float


class TranscriptEventSchema(BaseModel):
    id: str
    session_id: str
    speaker_role: SpeakerRole
    text: str
    is_final: bool
    confidence: float
    timestamp: str
    word_timestamps: list[WordTimestamp] | None = None
    is_retroactive: bool = False
    channel: str  # 'tab' | 'microphone'

    model_config = {"from_attributes": True}
