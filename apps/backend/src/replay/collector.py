"""Headless capture of everything a session pipeline does outside itself during replay."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from src.services.session_state_service import SessionStateService
from src.websocket.manager import dashboard_manager


@dataclass
class CapturedEvent:
    method: str
    session_id: str
    payload: Any
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class PersistedWrite:
    event_type: str
    session_id: str
    fields: dict[str, Any]


_CAPTURED_METHODS = (
    "send_transcript",
    "send_analysis",
    "send_red_flag",
    "send_hint",
    "send_speaker_snapshot",
    "send_code_analysis",
)


class _RecordingPersistence:
    """Stands in for PersistenceService: records each enqueue instead of writing to the DB."""

    def __init__(self, sink: list[PersistedWrite]) -> None:
        self._sink = sink

    def __getattr__(self, name: str):
        if not name.startswith("enqueue_"):
            raise AttributeError(name)
        event_type = name.removeprefix("enqueue_")

        def _enqueue(session_id: str, **fields: Any) -> None:
            self._sink.append(PersistedWrite(event_type, session_id, fields))

        return _enqueue


class EventCollector:
    """Async context manager that captures a pipeline's outbound effects.

    - every ``dashboard_manager.send_*`` broadcast (patched on the shared
      manager object, so it works for any pipeline implementation);
    - every persistence enqueue (``safe_enqueue`` resolves the persistence
      service through ``src.dependencies`` at call time);
    - session-state updates, into a private in-memory ``SessionStateService``
      (``session_state``) instead of the app's.
    """

    def __init__(self) -> None:
        self.events: list[CapturedEvent] = []
        self.persisted: list[PersistedWrite] = []
        self.session_state = SessionStateService()
        self._patchers: list = []

    async def __aenter__(self) -> "EventCollector":
        persistence = _RecordingPersistence(self.persisted)
        self._patchers = [patch.object(dashboard_manager, m, self._make_capture(m)) for m in _CAPTURED_METHODS]
        self._patchers += [
            patch("src.dependencies.get_persistence_service", lambda: persistence),
            patch("src.dependencies.get_session_state_service", lambda: self.session_state),
        ]
        for p in self._patchers:
            p.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        for p in self._patchers:
            p.stop()
        self._patchers = []

    def _make_capture(self, method: str):
        async def _capture(session_id: str, payload: Any) -> None:
            self.events.append(CapturedEvent(method=method, session_id=session_id, payload=payload))

        return _capture

    def filter(self, method: str) -> list[CapturedEvent]:
        return [e for e in self.events if e.method == method]

    def to_dict(self) -> list[dict]:
        return [
            {
                "method": e.method,
                "session_id": e.session_id,
                "payload": e.payload,
                "timestamp": e.timestamp,
            }
            for e in self.events
        ]
