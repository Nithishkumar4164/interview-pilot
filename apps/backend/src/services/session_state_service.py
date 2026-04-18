import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.resilience import SessionSnapshot
from src.utils.logging import get_logger

logger = get_logger("session_state")

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_INTERVAL_S = 30


class SessionState:
    """In-memory state for a single active interview session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.transcript_count: int = 0
        self.last_transcript_timestamp: str | None = None
        self.analysis_count: int = 0
        self.code_snapshot_count: int = 0
        self.hint_history: list[dict[str, Any]] = []
        self.active_question_id: str | None = None
        self.scores: dict[str, float] = {}
        self.coverage_tracker: dict[str, bool] = {}
        self.plan_progress: dict[str, int] = {
            "current_phase_index": 0,
            "current_question_index": 0,
        }
        self.facts: dict[str, Any] = {}
        self.rolling_summary: str = ""
        self._dirty: bool = False

    def mark_dirty(self) -> None:
        self._dirty = True

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def to_snapshot(self) -> dict[str, Any]:
        self._dirty = False
        return {
            "session_id": self.session_id,
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "transcript_count": self.transcript_count,
            "last_transcript_timestamp": self.last_transcript_timestamp,
            "analysis_count": self.analysis_count,
            "code_snapshot_count": self.code_snapshot_count,
            "hint_history": self.hint_history,
            "active_question_id": self.active_question_id,
            "scores": self.scores,
            "coverage_tracker": self.coverage_tracker,
            "plan_progress": self.plan_progress,
            "facts": self.facts,
            "rolling_summary": self.rolling_summary,
            "captured_at": datetime.now(UTC).isoformat(),
        }

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> "SessionState":
        state = cls(data["session_id"])
        state.transcript_count = data.get("transcript_count", 0)
        state.last_transcript_timestamp = data.get("last_transcript_timestamp")
        state.analysis_count = data.get("analysis_count", 0)
        state.code_snapshot_count = data.get("code_snapshot_count", 0)
        state.hint_history = data.get("hint_history", [])
        state.active_question_id = data.get("active_question_id")
        state.scores = data.get("scores", {})
        state.coverage_tracker = data.get("coverage_tracker", {})
        state.plan_progress = data.get("plan_progress", {"current_phase_index": 0, "current_question_index": 0})
        state.facts = data.get("facts", {})
        state.rolling_summary = data.get("rolling_summary", "")
        return state


class SessionStateService:
    """Manages session state with periodic snapshot persistence.

    - Maintains in-memory state per active session
    - Writes snapshots to PostgreSQL every 30s and on significant events
    - Recovers sessions from latest snapshot on startup
    """

    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {}
        self._snapshot_task: asyncio.Task | None = None

    def get_state(self, session_id: str) -> SessionState:
        if session_id not in self._states:
            self._states[session_id] = SessionState(session_id)
        return self._states[session_id]

    def get_state_or_none(self, session_id: str) -> SessionState | None:
        """Return the cached state for ``session_id`` without creating a new
        empty one. Callers that just want to read should use this instead
        of reaching into ``_states``.
        """
        return self._states.get(session_id)

    def remove_state(self, session_id: str) -> None:
        self._states.pop(session_id, None)

    def update_from_worker(self, session_id: str, worker_data: dict) -> None:
        """Update session state from AnalysisWorker.to_dict() output.

        Populates transcript_count, analysis_count, scores, rolling_summary,
        and facts from the worker's current state.
        """
        state = self.get_state(session_id)

        state.transcript_count = worker_data.get("events_processed", state.transcript_count)
        state.analysis_count = worker_data.get("analyses_run", state.analysis_count)

        # Extract memory data
        memory = worker_data.get("memory", {})
        if memory:
            fact_store = memory.get("fact_store", {})
            if fact_store:
                state.facts = fact_store
            rolling_summary = memory.get("rolling_summary", "")
            if rolling_summary:
                state.rolling_summary = rolling_summary

        # Extract analysis scores
        analysis = worker_data.get("analysis", {})
        if analysis:
            recent = analysis.get("recent_analyses", [])
            if recent:
                scored = [a for a in recent if a.get("answer_quality", 0) > 0]
                if scored:
                    avg = sum(a["answer_quality"] for a in scored) / len(scored)
                    state.scores["problem_solving"] = round(avg, 1)

        state.mark_dirty()

    async def save_snapshot(self, session_id: str, db: AsyncSession) -> None:
        """Persist current state as a snapshot to PostgreSQL."""
        state = self._states.get(session_id)
        if state is None:
            return

        snapshot = SessionSnapshot(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session_id),
            snapshot_data=state.to_snapshot(),
            schema_version=SNAPSHOT_SCHEMA_VERSION,
        )
        db.add(snapshot)
        await db.flush()
        logger.info("snapshot_saved", session_id=session_id)

    async def recover_session(self, session_id: str, db: AsyncSession) -> SessionState | None:
        """Recover session state from the latest snapshot."""
        result = await db.execute(
            select(SessionSnapshot)
            .where(SessionSnapshot.session_id == uuid.UUID(session_id))
            .order_by(SessionSnapshot.created_at.desc())
            .limit(1)
        )
        snapshot = result.scalar_one_or_none()

        if snapshot is None:
            logger.info("no_snapshot_found", session_id=session_id)
            return None

        state = SessionState.from_snapshot(snapshot.snapshot_data)
        self._states[session_id] = state
        logger.info("session_recovered", session_id=session_id)
        return state

    async def _periodic_snapshot_loop(self, db_factory) -> None:
        """Background loop that saves snapshots for dirty sessions."""
        while True:
            await asyncio.sleep(SNAPSHOT_INTERVAL_S)
            for session_id, state in list(self._states.items()):
                if state.is_dirty:
                    try:
                        async with db_factory() as db:
                            await self.save_snapshot(session_id, db)
                            await db.commit()
                    except Exception as e:
                        logger.error("snapshot_failed", session_id=session_id, error=str(e))

    def start_periodic_snapshots(self, db_factory) -> None:
        if self._snapshot_task is None or self._snapshot_task.done():
            self._snapshot_task = asyncio.create_task(self._periodic_snapshot_loop(db_factory))

    def stop_periodic_snapshots(self) -> None:
        if self._snapshot_task and not self._snapshot_task.done():
            self._snapshot_task.cancel()
