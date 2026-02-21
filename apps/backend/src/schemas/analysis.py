"""Structured analysis schemas — 6-dimension insight evaluation.

Provides multi-dimensional scoring of candidate responses with
backward-compatible top-level fields.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class DimensionScore(str, Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    ADEQUATE = "adequate"
    WEAK = "weak"
    POOR = "poor"


class RelevanceDimension(BaseModel):
    score: DimensionScore
    on_topic: bool
    justification: str


class BehavioralSignalDimension(BaseModel):
    score: DimensionScore
    signals_detected: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    justification: str


class ContextualConsistencyDimension(BaseModel):
    score: DimensionScore
    consistent: bool
    contradictions: list[str] = Field(default_factory=list)
    justification: str


class HintReceptionDimension(BaseModel):
    score: DimensionScore
    acted_on_hint: bool
    incorporation_level: Literal["full", "partial", "none"]
    justification: str


class DepthCompletenessDimension(BaseModel):
    score: DimensionScore
    key_points_covered: list[str] = Field(default_factory=list)
    key_points_missed: list[str] = Field(default_factory=list)
    justification: str


class CommunicationQualityDimension(BaseModel):
    score: DimensionScore
    clarity: Literal["high", "medium", "low"]
    structure: Literal["high", "medium", "low"]
    confidence: Literal["high", "medium", "low"]
    justification: str


class InsightDimensions(BaseModel):
    relevance: RelevanceDimension
    behavioral_signal: BehavioralSignalDimension
    contextual_consistency: ContextualConsistencyDimension
    hint_reception: HintReceptionDimension | None = None
    depth_completeness: DepthCompletenessDimension
    communication_quality: CommunicationQualityDimension


class EnhancedAnalysisResult(BaseModel):
    """Structured result from a real-time analysis.

    Backward-compatible: top-level fields (answer_quality, on_track, etc.)
    are always populated. The `dimensions` field carries the richer
    6-dimension evaluation when available.
    """

    # Backward-compatible top-level fields
    answer_quality: int = 3
    on_track: bool = True
    insight: str = ""
    red_flags: list[str] = Field(default_factory=list)
    suggestion_for_interviewer: str = ""
    hint_signal: str = "none"

    # Enhanced dimensions
    dimensions: InsightDimensions | None = None

    # Metadata
    timestamp: str = ""
    latency_ms: float = 0
    provider: str = ""
