"""Code WebSocket handler.

Receives code snapshots from the Chrome extension,
routes them through the code analysis service.

Endpoint: ws://{host}/ws/code/{session_id}
"""

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.utils.logging import get_logger
from src.websocket.manager import dashboard_manager

logger = get_logger("ws.code")

router = APIRouter()

# Active code analysis sessions — managed by the session lifecycle
_active_analyzers: dict = {}


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
    await websocket.accept()

    logger.info("code_ws_connected", session_id=session_id)

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

                elif msg_type == "code_snapshot":
                    code = message.get("code", "")
                    language = message.get("language", "unknown")
                    source = message.get("source", "unknown")

                    # Get the analyzer for this session
                    analyzer = _active_analyzers.get(session_id)
                    if analyzer:
                        result = await analyzer.analyze_snapshot(code, language)

                        # Push code analysis to dashboard
                        await dashboard_manager.send_code_analysis(
                            session_id,
                            {
                                "code_length": len(code),
                                "language": language,
                                "source": source,
                                **result.to_dict(),
                            },
                        )
                    else:
                        # No active analyzer — just forward the snapshot info
                        await dashboard_manager.send_code_analysis(
                            session_id,
                            {
                                "code_length": len(code),
                                "language": language,
                                "source": source,
                                "progress_state": "unknown",
                            },
                        )

    except WebSocketDisconnect:
        logger.info("code_ws_disconnected", session_id=session_id)
    except Exception as e:
        logger.error(
            "code_ws_error",
            session_id=session_id,
            error=str(e),
        )


def register_analyzer(session_id: str, analyzer: object) -> None:
    """Register a code analyzer for a session."""
    _active_analyzers[session_id] = analyzer


def unregister_analyzer(session_id: str) -> None:
    """Remove a code analyzer for a session."""
    _active_analyzers.pop(session_id, None)
