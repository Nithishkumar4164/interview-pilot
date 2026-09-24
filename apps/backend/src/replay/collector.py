"""Headless capture of dashboard_manager broadcasts during replay."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch


@dataclass
class CapturedEvent:
    method: str
    session_id: str
    payload: Any
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


_CAPTURED_METHODS = (
    "send_transcript",
    "send_analysis",
    "send_red_flag",
    "send_hint",
    "send_speaker_snapshot",
)


def _noop(*args, **kwargs) -> None:
    return None


class EventCollector:
    """Async context manager that captures every dashboard broadcast.

    Patches `src.workers.analysis_worker.dashboard_manager` for the duration
    of the context. All `send_*` calls become AsyncMock wrappers that append
    to `self.events` instead of broadcasting to WebSocket clients.

    Also no-ops `safe_enqueue` and `safe_update_session_state` so replay
    runs don't try to persist transcripts under a non-UUID session id.
    """

    def __init__(self) -> None:
        self.events: list[CapturedEvent] = []
        self._patchers: list = []

    async def __aenter__(self) -> "EventCollector":
        mock_mgr = AsyncMock()
        for method_name in _CAPTURED_METHODS:
            setattr(
                mock_mgr,
                method_name,
                self._make_capture(method_name),
            )

        self._patchers = [
            patch("src.workers.analysis_worker.dashboard_manager", mock_mgr),
            patch("src.workers.analysis_worker.safe_enqueue", _noop),
            patch("src.workers.analysis_worker.safe_update_session_state", _noop),
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
