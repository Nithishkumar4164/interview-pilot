"""Audio WebSocket handler.

Receives binary audio frames from the Chrome extension,
routes them through the transcription service pipeline.

Endpoint: ws://{host}/ws/audio/{session_id}
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.dependencies import failover_manager
from src.services.transcription_service import TranscriptionService
from src.utils.logging import get_logger
from src.workers.analysis_worker import AnalysisWorker

logger = get_logger("ws.audio")

router = APIRouter()

# Active transcription sessions (shared with other handlers)
_active_sessions: dict[str, TranscriptionService] = {}
_active_workers: dict[str, AnalysisWorker] = {}


def get_transcription_service(session_id: str) -> TranscriptionService:
    """Get or create a TranscriptionService for a session."""
    if session_id not in _active_sessions:
        service = TranscriptionService(session_id)
        worker = AnalysisWorker(session_id, failover_manager)
        service.on_event(worker.on_transcript_event)
        _active_sessions[session_id] = service
        _active_workers[session_id] = worker
        logger.info("analysis_worker_created", session_id=session_id)
    return _active_sessions[session_id]


def remove_transcription_service(session_id: str) -> None:
    """Remove a TranscriptionService for a session."""
    worker = _active_workers.pop(session_id, None)
    if worker:
        worker.stop()
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

    binary_frame_count = 0
    try:
        while True:
            data = await websocket.receive()

            if "bytes" in data:
                # Binary audio frame
                binary_frame_count += 1
                if binary_frame_count == 1:
                    logger.info(
                        "audio_streaming_started",
                        session_id=session_id,
                        frame_size=len(data["bytes"]),
                    )
                await service.process_audio_frame(data["bytes"])

            elif "text" in data:
                # JSON control messages
                import json

                try:
                    message = json.loads(data["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = message.get("type")
                logger.info("audio_ws_text_msg", session_id=session_id, msg_type=msg_type, detail=message.get("event") or message.get("error"))

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
