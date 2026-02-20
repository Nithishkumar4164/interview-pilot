"""Dashboard WebSocket handler.

Pushes real-time events (transcripts, analysis, hints, code analysis)
to dashboard clients. Supports multiple tabs per session.

Endpoint: ws://{host}/ws/dashboard/{session_id}
"""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.utils.logging import get_logger
from src.websocket.manager import dashboard_manager

logger = get_logger("ws.dashboard")

router = APIRouter()


@router.websocket("/ws/dashboard/{session_id}")
async def dashboard_websocket(websocket: WebSocket, session_id: str) -> None:
    """Handle dashboard WebSocket connections.

    The dashboard receives all real-time events for a session:
    - transcript: live transcription updates
    - analysis: AI analysis of utterances
    - code_analysis: code review events
    - hint: progressive hint events
    - red_flag: contradiction / concern alerts
    - catchup: retroactive catch-up summaries
    - provider_status: LLM provider failover notifications
    - error: error messages

    Clients can also send control messages:
    - ping: heartbeat (server responds with pong)
    - subscribe_events: filter which event types to receive
    """
    await dashboard_manager.connect(session_id, websocket)

    # Per-connection event filter (empty = receive all)
    subscribed_types: set[str] = set()

    try:
        while True:
            data = await websocket.receive()

            if "text" in data:
                try:
                    message = json.loads(data["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = message.get("type")

                if msg_type == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": message.get("timestamp"),
                    })

                elif msg_type == "subscribe_events":
                    # Allow clients to filter event types
                    event_types = message.get("event_types", [])
                    if isinstance(event_types, list):
                        subscribed_types = set(event_types)
                        await websocket.send_json({
                            "type": "subscribed",
                            "event_types": list(subscribed_types),
                        })

    except WebSocketDisconnect:
        logger.info(
            "dashboard_ws_disconnected",
            session_id=session_id,
        )
    except Exception as e:
        logger.error(
            "dashboard_ws_error",
            session_id=session_id,
            error=str(e),
        )
    finally:
        await dashboard_manager.disconnect(session_id, websocket)
