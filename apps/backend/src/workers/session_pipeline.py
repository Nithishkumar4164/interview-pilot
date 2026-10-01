"""The seam between transport (WebSocket/REST handlers) and the real-time pipeline.

Handlers talk to a session's pipeline only through this Protocol, so the
implementation (today ``AnalysisWorker``; later the LangGraph session graph)
can be swapped by ``create_session_pipeline`` without touching call sites.
"""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from src.llm.failover import FailoverManager
from src.schemas.transcript import TranscriptEventSchema


@runtime_checkable
class SessionPipeline(Protocol):
    session_id: str

    # ── Inbound events ────────────────────────────────────────────────
    async def on_transcript_event(self, event: TranscriptEventSchema) -> None: ...

    async def on_question_change(self, question_text: str, question_plan: dict, question_index: int = 0) -> None: ...

    def set_active_hint(self, hint_text: str) -> None: ...

    async def handle_runtime_hint_ack(self, data: dict) -> None: ...

    # ── Request/response ──────────────────────────────────────────────
    async def handle_runtime_hint_request(self, request_data: dict) -> dict: ...

    async def handle_code_rating(self, data: dict) -> dict: ...

    # ── Reads ─────────────────────────────────────────────────────────
    def get_recent_transcripts(self, limit: int = 50) -> list[dict]: ...

    def get_transcripts_by_ids(self, transcript_ids: set[str]) -> list[dict]: ...

    def report_snapshot(self) -> dict:
        """Session-derived inputs for report generation (facts, summary, analyses, contradictions)."""
        ...

    def to_dict(self) -> dict: ...

    # ── Lifecycle ─────────────────────────────────────────────────────
    async def drain(self) -> None: ...

    async def wait_idle(self) -> None:
        """Wait until all work triggered so far has finished (replay and tests)."""
        ...

    def stop(self) -> None: ...

    async def close(self) -> None:
        """The session ended: stop for good and release background work and stored state."""
        ...


def create_session_pipeline(
    session_id: str,
    failover_manager: FailoverManager,
    get_speaker_snapshot: Callable[[], dict] | None = None,
) -> SessionPipeline:
    from src.config import settings

    if settings.use_langgraph_session:
        from src.dependencies import get_checkpointer
        from src.workers.langgraph_session import LangGraphSessionPipeline

        return LangGraphSessionPipeline(
            session_id, failover_manager, get_checkpointer(), get_speaker_snapshot=get_speaker_snapshot
        )

    from src.workers.analysis_worker import AnalysisWorker

    return AnalysisWorker(session_id, failover_manager, get_speaker_snapshot=get_speaker_snapshot)
