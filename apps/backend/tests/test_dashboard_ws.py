"""The dashboard WebSocket through the real app: a page reload closes it cleanly.

A browser reload (or closing the tab) sends a normal close. That must be
logged as a disconnect, not as ``dashboard_ws_error``.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from src.main import app
from src.websocket import dashboard_handler


def test_a_closed_page_is_a_disconnect_not_an_error(monkeypatch) -> None:
    monkeypatch.setattr(dashboard_handler, "authenticate_ws", AsyncMock(return_value=uuid.uuid4()))
    log = MagicMock()
    monkeypatch.setattr(dashboard_handler, "logger", log)
    session_id = str(uuid.uuid4())

    with TestClient(app).websocket_connect(f"/ws/dashboard/{session_id}") as ws:
        ws.send_json({"type": "ping", "timestamp": 1})
        assert ws.receive_json() == {"type": "pong", "timestamp": 1}
    # Leaving the block closes the socket the way a reload does.

    log.error.assert_not_called()
    log.info.assert_any_call("dashboard_ws_disconnected", session_id=session_id)


def test_an_extension_closing_its_code_socket_is_a_disconnect(monkeypatch) -> None:
    from src.websocket import code_handler

    pipeline = object()
    monkeypatch.setattr(code_handler, "authenticate_ws", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(code_handler, "session_has_ended", AsyncMock(return_value=False))
    monkeypatch.setattr(code_handler, "get_session_pipeline", lambda _sid: pipeline)
    monkeypatch.setattr(code_handler, "active_pipeline", lambda _sid: pipeline)
    log = MagicMock()
    monkeypatch.setattr(code_handler, "logger", log)
    session_id = str(uuid.uuid4())

    with TestClient(app).websocket_connect(f"/ws/code/{session_id}") as ws:
        ws.send_json({"type": "ping", "timestamp": 1})
        assert ws.receive_json() == {"type": "pong", "timestamp": 1}

    log.error.assert_not_called()
    log.info.assert_any_call("code_ws_disconnected", session_id=session_id)


def test_an_extension_closing_its_audio_socket_is_a_disconnect(monkeypatch) -> None:
    from src.websocket import audio_handler

    service = MagicMock(is_running=True)
    monkeypatch.setattr(audio_handler, "authenticate_ws", AsyncMock(return_value=uuid.uuid4()))
    monkeypatch.setattr(audio_handler, "session_has_ended", AsyncMock(return_value=False))
    monkeypatch.setattr(audio_handler, "get_transcription_service", lambda _sid: service)
    log = MagicMock()
    monkeypatch.setattr(audio_handler, "logger", log)
    session_id = str(uuid.uuid4())

    with TestClient(app).websocket_connect(f"/ws/audio/{session_id}") as ws:
        ws.send_json({"type": "ping", "timestamp": 1})
        ws.receive_json()

    log.error.assert_not_called()
    log.info.assert_any_call("audio_ws_disconnected", session_id=session_id)
