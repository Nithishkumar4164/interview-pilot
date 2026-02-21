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

    # Send catch-up transcripts if session is already active
    from src.websocket.audio_handler import _active_workers, _active_sessions

    worker = _active_workers.get(session_id)
    if worker:
        recent = worker.get_recent_transcripts()
        if recent:
            await dashboard_manager.send_catchup(session_id, {
                "events": recent,
            })
            logger.info(
                "dashboard_catchup_sent",
                session_id=session_id,
                event_count=len(recent),
            )

    # Send speaker snapshot on connect
    service = _active_sessions.get(session_id)
    if service:
        snapshot = service.speaker_service.get_speaker_snapshot()
        await dashboard_manager.send_speaker_snapshot(session_id, snapshot)

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

                # ── Speaker management handlers ──────────────────────
                elif msg_type == "get_speaker_snapshot":
                    svc = _active_sessions.get(session_id)
                    if svc:
                        snap = svc.speaker_service.get_speaker_snapshot()
                    else:
                        # No audio session yet — return defaults so the
                        # panel is never empty.
                        from src.services.speaker_service import SpeakerService
                        snap = SpeakerService(session_id).get_speaker_snapshot()
                    await dashboard_manager.send_speaker_snapshot(session_id, snap)

                elif msg_type == "create_root_speaker":
                    svc = _active_sessions.get(session_id)
                    if svc:
                        svc.speaker_service.create_root_speaker(
                            label=message.get("label", "Speaker"),
                            color=message.get("color"),
                        )
                        snap = svc.speaker_service.get_speaker_snapshot()
                        await dashboard_manager.send_speaker_snapshot(session_id, snap)

                elif msg_type == "rename_root_speaker":
                    svc = _active_sessions.get(session_id)
                    if svc:
                        svc.speaker_service.rename_root_speaker(
                            rs_id=message.get("rs_id", ""),
                            label=message.get("label", ""),
                        )
                        snap = svc.speaker_service.get_speaker_snapshot()
                        await dashboard_manager.send_speaker_snapshot(session_id, snap)

                elif msg_type == "delete_root_speaker":
                    svc = _active_sessions.get(session_id)
                    if svc:
                        svc.speaker_service.delete_root_speaker(
                            rs_id=message.get("rs_id", ""),
                        )
                        snap = svc.speaker_service.get_speaker_snapshot()
                        await dashboard_manager.send_speaker_snapshot(session_id, snap)

                elif msg_type == "reassign_speech_vector":
                    svc = _active_sessions.get(session_id)
                    if svc:
                        svc.speaker_service.reassign_speech_vector(
                            sv_id=message.get("sv_id", ""),
                            target_rs_id=message.get("target_rs_id", ""),
                        )
                        snap = svc.speaker_service.get_speaker_snapshot()
                        await dashboard_manager.send_speaker_snapshot(session_id, snap)

                elif msg_type == "reassign_transcript":
                    svc = _active_sessions.get(session_id)
                    if svc:
                        new_sv_id = svc.speaker_service.reassign_transcript(
                            transcript_id=message.get("transcript_id", ""),
                            source_sv_id=message.get("source_sv_id", ""),
                            target_rs_id=message.get("target_rs_id", ""),
                        )
                        await dashboard_manager.send_speaker_transcript_assigned(
                            session_id,
                            {
                                "transcript_id": message.get("transcript_id", ""),
                                "new_root_speaker_id": message.get("target_rs_id", ""),
                                "new_speech_vector_id": new_sv_id,
                            },
                        )
                        # Also broadcast updated snapshot
                        snap = svc.speaker_service.get_speaker_snapshot()
                        await dashboard_manager.send_speaker_snapshot(session_id, snap)

                # ── Code rating handler ───────────────────────────
                elif msg_type == "rate_code":
                    worker = _active_workers.get(session_id)
                    if worker:
                        try:
                            rating_result = await worker.handle_code_rating(message)
                            await dashboard_manager.send_code_rating(
                                session_id, rating_result,
                            )
                        except Exception as e:
                            logger.error(
                                "code_rating_error",
                                session_id=session_id,
                                error=str(e),
                            )
                            await dashboard_manager.send_error(
                                session_id, f"Code rating failed: {e}",
                            )

                # ── Runtime hint handlers ─────────────────────────
                elif msg_type == "request_runtime_hint":
                    worker = _active_workers.get(session_id)
                    if worker:
                        try:
                            hint_result = await worker.handle_runtime_hint_request(message)
                            await dashboard_manager.send_runtime_hint(
                                session_id, hint_result,
                            )
                        except Exception as e:
                            logger.error(
                                "runtime_hint_error",
                                session_id=session_id,
                                error=str(e),
                            )
                            await dashboard_manager.send_error(
                                session_id, f"Runtime hint failed: {e}",
                            )

                elif msg_type == "acknowledge_runtime_hint":
                    worker = _active_workers.get(session_id)
                    if worker:
                        try:
                            await worker.handle_runtime_hint_ack(message)
                        except Exception as e:
                            logger.error(
                                "runtime_hint_ack_error",
                                session_id=session_id,
                                error=str(e),
                            )

                elif msg_type == "change_question":
                    worker = _active_workers.get(session_id)
                    if worker:
                        await worker.on_question_change(
                            question_text=message.get("question_text", ""),
                            question_plan=message.get("question_plan") or {},
                            question_index=message.get("question_index", 0),
                        )
                        await dashboard_manager.send_context_reloaded(
                            session_id,
                            {"question_index": message.get("question_index", 0)},
                        )

                elif msg_type == "get_sv_transcripts":
                    sv_id = message.get("sv_id", "")
                    svc = _active_sessions.get(session_id)
                    worker = _active_workers.get(session_id)
                    if svc and worker and sv_id:
                        sv = svc.speaker_service._speech_vectors.get(sv_id)
                        if sv:
                            tid_set = set(sv.transcript_ids)
                            matching = [
                                {"id": t["id"], "text": t["text"]}
                                for t in worker._transcript_history
                                if t["id"] in tid_set
                            ]
                            await dashboard_manager.send_sv_transcripts(
                                session_id, sv_id, matching,
                            )

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
