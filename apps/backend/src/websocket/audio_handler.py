"""Audio WebSocket handler.

Receives binary audio frames from the Chrome extension,
routes them through the transcription service pipeline.

Endpoint: ws://{host}/ws/audio/{session_id}
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.services.transcription_service import TranscriptionService
from src.utils.logging import get_logger

logger = get_logger("ws.audio")

router = APIRouter()

# Active transcription sessions (shared with other handlers)
_active_sessions: dict[str, TranscriptionService] = {}


def get_transcription_service(session_id: str) -> TranscriptionService:
    """Get or create a TranscriptionService for a session."""
    if session_id not in _active_sessions:
        _active_sessions[session_id] = TranscriptionService(session_id)
    return _active_sessions[session_id]


def remove_transcription_service(session_id: str) -> None:
    """Remove a TranscriptionService for a session."""
    _active_sessions.pop(session_id, None)


@router.websocket("/ws/audio/{session_id}")
async def audio_websocket(websocket: WebSocket, session_id: str) -> None:
    """Handle audio WebSocket connections from the Chrome extension.

    Receives binary audio frames with a 12-byte header containing
    channel, timestamp, sample rate, and buffered flag.
    """
    await websocket.accept()

    service = get_transcription_service(session_id)

    # Start the Deepgram pipeline if not already running
    if not service.is_running:
        try:
            await service.start()
        except Exception as e:
            logger.error(
                "transcription_start_failed",
                session_id=session_id,
                error=str(e),
            )
            await websocket.close(code=1011, reason=f"Failed to start transcription: {e}")
            return

    logger.info(
        "audio_ws_connected",
        session_id=session_id,
    )

    try:
        while True:
            data = await websocket.receive()

            if "bytes" in data:
                # Binary audio frame
                await service.process_audio_frame(data["bytes"])

            elif "text" in data:
                # JSON control messages
                import json

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

                elif msg_type == "calibrate_speakers":
                    service.calibrate_speakers(
                        interviewer_speaker_id=message["interviewer_speaker_id"],
                        candidate_speaker_id=message.get("candidate_speaker_id"),
                    )
                    await websocket.send_json({
                        "type": "calibration_complete",
                        "speakers": service.get_detected_speakers(),
                    })

                elif msg_type == "get_speakers":
                    await websocket.send_json({
                        "type": "detected_speakers",
                        "speakers": service.get_detected_speakers(),
                    })

    except WebSocketDisconnect:
        logger.info(
            "audio_ws_disconnected",
            session_id=session_id,
        )
    except Exception as e:
        logger.error(
            "audio_ws_error",
            session_id=session_id,
            error=str(e),
        )
    finally:
        # Don't stop the transcription service on disconnect —
        # the extension may reconnect. The session end endpoint
        # will handle cleanup.
        pass
