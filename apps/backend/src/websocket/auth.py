"""WebSocket authentication helpers.

WebSocket endpoints can't use the standard FastAPI bearer dependency
because browsers won't set ``Authorization`` on the initial upgrade.
Clients pass the JWT as a ``?token=`` query parameter on the upgrade URL.

In development with ``settings.dev_mode`` + ``environment=development``
we skip auth entirely so local workflows keep working without managing
tokens. The :class:`Settings` post-init validator guarantees this cannot
be enabled in production.
"""

import uuid

from fastapi import WebSocket, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.auth import DEV_USER_ID
from src.config import settings
from src.dependencies import async_session_factory
from src.services.auth_service import AuthService
from src.utils.logging import get_logger

logger = get_logger("ws.auth")

# 1008 is the RFC-6455 "Policy Violation" code, the canonical choice
# for auth failures. Browser clients translate it to a close event.
WS_AUTH_CLOSE_CODE = status.WS_1008_POLICY_VIOLATION


def _extract_token(websocket: WebSocket) -> str | None:
    """Pull the bearer token off the WebSocket upgrade request.

    We only support ``?token=<jwt>`` as a query parameter. Prior drafts
    also accepted a ``Sec-WebSocket-Protocol: bearer,<jwt>`` header, but
    the three handlers don't echo the subprotocol on ``accept()`` so
    browsers rejected that path anyway.
    """
    return websocket.query_params.get("token")


async def _verify_session_ownership(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    session_id: str,
) -> bool:
    """Return True if ``user_id`` owns ``session_id``."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return False

    from src.repositories.session_repo import SessionRepository

    async with session_factory() as db:
        session_repo = SessionRepository(db)
        session = await session_repo.get_by_id(sid)
        return bool(session and session.user_id == user_id)


async def authenticate_ws(websocket: WebSocket, session_id: str) -> uuid.UUID | None:
    """Authenticate a WebSocket upgrade for a given session.

    Returns the authenticated user's UUID on success.

    On failure, closes the WebSocket with :data:`WS_AUTH_CLOSE_CODE`
    (1008 Policy Violation) and returns ``None``. Callers must abort if
    the return value is ``None`` — they can't call ``accept()`` after a
    close.

    In dev mode, returns :data:`src.api.auth.DEV_USER_ID` without
    validating a token. Callers should treat the returned UUID as the
    request user.
    """
    if settings.dev_mode and settings.environment == "development":
        return DEV_USER_ID

    token = _extract_token(websocket)
    if not token:
        logger.warning("ws_auth_missing_token", session_id=session_id)
        await websocket.close(code=WS_AUTH_CLOSE_CODE, reason="Missing token")
        return None

    async with async_session_factory() as db:
        auth_service = AuthService(db)
        user_id_str = auth_service.verify_token(token)
        if user_id_str is None:
            logger.warning("ws_auth_invalid_token", session_id=session_id)
            await websocket.close(code=WS_AUTH_CLOSE_CODE, reason="Invalid token")
            return None

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError:
        await websocket.close(code=WS_AUTH_CLOSE_CODE, reason="Invalid token payload")
        return None

    if not await _verify_session_ownership(async_session_factory, user_uuid, session_id):
        logger.warning(
            "ws_auth_session_mismatch",
            session_id=session_id,
            user_id=str(user_uuid),
        )
        await websocket.close(code=WS_AUTH_CLOSE_CODE, reason="Session not owned by user")
        return None

    return user_uuid
