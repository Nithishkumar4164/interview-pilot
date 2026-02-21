"""WebSocket Connection Manager.

Manages active WebSocket connections per session and provides
broadcast/unicast messaging capabilities.
"""

import asyncio
import json
from datetime import UTC, datetime

from fastapi import WebSocket

from src.utils.logging import get_logger

logger = get_logger("ws.manager")


class ConnectionManager:
    """Manages WebSocket connections grouped by session ID.

    Supports multiple connections per session (e.g., multiple dashboard tabs)
    and provides typed broadcast methods.
    """

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        """Accept and register a WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            if session_id not in self._connections:
                self._connections[session_id] = []
            self._connections[session_id].append(websocket)

        logger.info(
            "ws_client_connected",
            session_id=session_id,
            total_connections=len(self._connections.get(session_id, [])),
        )

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        async with self._lock:
            conns = self._connections.get(session_id, [])
            if websocket in conns:
                conns.remove(websocket)
            if not conns:
                self._connections.pop(session_id, None)

        logger.info(
            "ws_client_disconnected",
            session_id=session_id,
            remaining=len(self._connections.get(session_id, [])),
        )

    def get_connection_count(self, session_id: str) -> int:
        """Get the number of active connections for a session."""
        return len(self._connections.get(session_id, []))

    def has_connections(self, session_id: str) -> bool:
        return bool(self._connections.get(session_id))

    async def broadcast(self, session_id: str, message: dict) -> None:
        """Send a JSON message to all connections for a session."""
        conns = self._connections.get(session_id, [])
        if not conns:
            return

        # Add timestamp if not present
        if "timestamp" not in message:
            message["timestamp"] = datetime.now(UTC).isoformat()

        text = json.dumps(message)
        disconnected: list[WebSocket] = []

        for ws in conns:
            try:
                await ws.send_text(text)
            except Exception:
                disconnected.append(ws)

        # Clean up dead connections
        if disconnected:
            async with self._lock:
                for ws in disconnected:
                    conns = self._connections.get(session_id, [])
                    if ws in conns:
                        conns.remove(ws)

    async def send_transcript(self, session_id: str, event: dict) -> None:
        """Send a transcript event to all dashboard connections."""
        if not self.has_connections(session_id):
            logger.warning(
                "transcript_dropped_no_clients",
                session_id=session_id,
                text_preview=event.get("text", "")[:50],
            )
        await self.broadcast(session_id, {
            "type": "transcript",
            "session_id": session_id,
            "payload": event,
        })

    async def send_analysis(self, session_id: str, analysis: dict) -> None:
        """Send an analysis event to all dashboard connections."""
        await self.broadcast(session_id, {
            "type": "analysis",
            "session_id": session_id,
            "payload": analysis,
        })

    async def send_code_analysis(self, session_id: str, analysis: dict) -> None:
        """Send a code analysis event."""
        await self.broadcast(session_id, {
            "type": "code_analysis",
            "session_id": session_id,
            "payload": analysis,
        })

    async def send_hint(self, session_id: str, hint: dict) -> None:
        """Send a hint event."""
        await self.broadcast(session_id, {
            "type": "hint",
            "session_id": session_id,
            "payload": hint,
        })

    async def send_red_flag(self, session_id: str, flag: dict) -> None:
        """Send a red flag event."""
        await self.broadcast(session_id, {
            "type": "red_flag",
            "session_id": session_id,
            "payload": flag,
        })

    async def send_catchup(self, session_id: str, summary: dict) -> None:
        """Send a catch-up summary after buffered replay."""
        await self.broadcast(session_id, {
            "type": "catchup",
            "session_id": session_id,
            "payload": summary,
        })

    async def send_provider_status(self, session_id: str, status: dict) -> None:
        """Send a provider failover status update."""
        await self.broadcast(session_id, {
            "type": "provider_status",
            "session_id": session_id,
            "payload": status,
        })

    async def send_error(self, session_id: str, error: str) -> None:
        """Send an error message."""
        await self.broadcast(session_id, {
            "type": "error",
            "session_id": session_id,
            "payload": {"message": error},
        })

    async def send_speaker_snapshot(self, session_id: str, snapshot: dict) -> None:
        """Send a full speaker snapshot to all dashboard connections."""
        await self.broadcast(session_id, {
            "type": "speaker_snapshot",
            "session_id": session_id,
            "payload": snapshot,
        })

    async def send_speaker_transcript_assigned(
        self, session_id: str, assignment: dict,
    ) -> None:
        """Send a transcript reassignment notification."""
        await self.broadcast(session_id, {
            "type": "speaker_transcript_assigned",
            "session_id": session_id,
            "payload": assignment,
        })

    async def send_runtime_hint(self, session_id: str, hint: dict) -> None:
        """Send a runtime hint response to all dashboard connections."""
        await self.broadcast(session_id, {
            "type": "runtime_hint",
            "session_id": session_id,
            "payload": hint,
        })

    async def send_sv_transcripts(
        self, session_id: str, sv_id: str, transcripts: list[dict],
    ) -> None:
        """Send recent transcript texts for a speech vector."""
        await self.broadcast(session_id, {
            "type": "sv_transcripts",
            "session_id": session_id,
            "payload": {"sv_id": sv_id, "transcripts": transcripts},
        })


# Global singleton
dashboard_manager = ConnectionManager()
