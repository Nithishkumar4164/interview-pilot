"""Code WebSocket handler.

Receives code snapshots from the Chrome extension and hands them to the
session's pipeline (code analysis, dashboard push, persistence).

Endpoint: ws://{host}/ws/code/{session_id}
"""

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from src.utils.logging import get_logger
from src.websocket.audio_handler import active_pipeline, get_session_pipeline
from src.websocket.auth import authenticate_ws, session_has_ended

logger = get_logger("ws.code")

router = APIRouter()


@router.websocket("/ws/code/{session_id}")
async def code_websocket(websocket: WebSocket, session_id: str) -> None:
    """Handle code WebSocket connections from the Chrome extension.

    Receives JSON messages with code snapshots:
    {
        "type": "code_snapshot",
        "code": "...",
        "language": "python",
        "source": "codemirror|monaco|ace|textarea",
        "timestamp": "..."
    }
    """
    if await authenticate_ws(websocket, session_id) is None:
        return
    if await session_has_ended(session_id):
        logger.info("code_ws_rejected_session_ended", session_id=session_id)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session ended")
        return
    await websocket.accept()

    logger.info("code_ws_connected", session_id=session_id)
    pipeline = get_session_pipeline(session_id)

    try:
        while True:
            data = await websocket.receive()
            # Raw receive() reports a close as a message rather than raising, and
            # calling it again after that raises RuntimeError.
            if data["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(data.get("code", 1000))

            if active_pipeline(session_id) is not pipeline:
                # The session ended (end-session closed its pipeline) while this socket was open.
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session ended")
                return

            if "text" in data:
                try:
                    message = json.loads(data["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = message.get("type")

                if msg_type == "ping":
                    await websocket.send_json(
                        {
                            "type": "pong",
                            "timestamp": message.get("timestamp"),
                        }
                    )

                elif msg_type in ("code_snapshot", "code_update"):
                    # Extract from payload envelope (extension wraps in payload)
                    payload = message.get("payload", message)
                    await pipeline.on_code_snapshot(
                        payload.get("code", ""),
                        payload.get("language", "unknown"),
                        payload.get("source", "unknown"),
                    )

    except WebSocketDisconnect:
        logger.info("code_ws_disconnected", session_id=session_id)
    except Exception as e:
        logger.error(
            "code_ws_error",
            session_id=session_id,
            error=str(e),
        )
