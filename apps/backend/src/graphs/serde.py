"""Checkpoint serialization with an explicit type allowlist.

LangGraph's default serializer deserializes any custom type it finds in a
checkpoint, with a warning that this "will be blocked in a future version".
Relying on that would let a routine LangGraph upgrade silently break session
recovery. Instead every custom type stored in graph state is registered here,
and anything else is refused now — so a missing registration fails in tests,
not in a production resume.

When a new type is added to any graph's state, add it to CHECKPOINT_TYPES.
"""

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from src.schemas.analysis import (
    BehavioralSignalDimension,
    CommunicationQualityDimension,
    ContextualConsistencyDimension,
    DepthCompletenessDimension,
    DimensionScore,
    EnhancedAnalysisResult,
    HintReceptionDimension,
    InsightDimensions,
    RelevanceDimension,
)
from src.services.contradiction_service import Contradiction
from src.services.memory_service import FactStore, Utterance

CHECKPOINT_TYPES: tuple[type, ...] = (
    # analysis results
    EnhancedAnalysisResult,
    InsightDimensions,
    RelevanceDimension,
    BehavioralSignalDimension,
    ContextualConsistencyDimension,
    HintReceptionDimension,
    DepthCompletenessDimension,
    CommunicationQualityDimension,
    DimensionScore,
    # memory tiers
    Utterance,
    FactStore,
    # contradictions
    Contradiction,
)


def allowed_msgpack_modules() -> list[tuple[str, str]]:
    return [(t.__module__, t.__qualname__) for t in CHECKPOINT_TYPES]


def build_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=allowed_msgpack_modules())
