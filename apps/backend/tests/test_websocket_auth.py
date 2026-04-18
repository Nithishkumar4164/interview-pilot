"""Tests for WebSocket upgrade authentication (B4).

Verifies:
- dev_mode shortcut returns the dev-user UUID without a token.
- Non-dev environments close the socket with a policy-violation code
  when no token is provided.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.websocket import auth as ws_auth
from src.websocket.auth import authenticate_ws


def _fake_ws(token: str | None = None) -> MagicMock:
    ws = MagicMock()
    ws.query_params = {"token": token} if token else {}
    ws.headers = {}
    ws.close = AsyncMock()
    ws.accept = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_authenticate_ws_returns_dev_user_in_dev_mode(monkeypatch):
    # Default Settings has dev_mode=True + environment="development"
    ws = _fake_ws()
    user_id = await authenticate_ws(ws, "any-session-id")
    assert user_id == uuid.UUID("00000000-0000-0000-0000-000000000000")
    ws.close.assert_not_called()


@pytest.mark.asyncio
async def test_authenticate_ws_rejects_missing_token_in_prod(monkeypatch):
    monkeypatch.setattr(ws_auth.settings, "dev_mode", False)
    monkeypatch.setattr(ws_auth.settings, "environment", "production")
    ws = _fake_ws(token=None)
    user_id = await authenticate_ws(ws, "any-session-id")
    assert user_id is None
    ws.close.assert_awaited_once()
    close_kwargs = ws.close.await_args
    assert close_kwargs.kwargs["code"] == ws_auth.WS_AUTH_CLOSE_CODE


@pytest.mark.asyncio
async def test_authenticate_ws_rejects_invalid_token_in_prod(monkeypatch):
    monkeypatch.setattr(ws_auth.settings, "dev_mode", False)
    monkeypatch.setattr(ws_auth.settings, "environment", "production")

    class FakeAuthService:
        def __init__(self, db):
            pass

        def verify_token(self, token):
            return None

    monkeypatch.setattr(ws_auth, "AuthService", FakeAuthService)

    class FakeSessionCtx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr(ws_auth, "async_session_factory", lambda: FakeSessionCtx())

    ws = _fake_ws(token="bogus")
    user_id = await authenticate_ws(ws, "any-session-id")
    assert user_id is None
    ws.close.assert_awaited_once()
