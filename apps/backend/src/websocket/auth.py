"""WebSocket authentication helpers.

WebSocket endpoints can't use the standard FastAPI bearer dependency
because browsers won't set ``Authorization`` on the initial upgrade.
Clients pass the JWT as a ``?token=`` query parameter on the upgrade URL.

In development with ``settings.dev_mode`` + ``environment=development``
a connection without a token is treated as the seeded dev user. The
:class:`Settings` post-init validator guarantees this cannot be enabled in
production.
"""

import uuid

from fastapi import WebSocket, status

from src.dependencies import async_session_factory
from src.models.session import InterviewSession
from src.services.access_control import DEV_USER_ID, dev_mode_active, principal_from_token
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


async def authenticate_ws(websocket: WebSocket, session_id: str) -> uuid.UUID | None:
    """Authenticate a WebSocket upgrade for a given session.

    Accepts a user access token (owner or admin of the session) or a pairing
    token minted for exactly this session. Returns the authenticated user's
    UUID on success.

    On failure, closes the WebSocket with :data:`WS_AUTH_CLOSE_CODE`
    (1008 Policy Violation) and returns ``None``. Callers must abort if
    the return value is ``None`` — they can't call ``accept()`` after a
    close.

    A presented token is always validated; with no token, dev mode falls
    back to the seeded dev user.
    """
    token = _extract_token(websocket)
    if not token:
        if dev_mode_active():
            return DEV_USER_ID
        logger.warning("ws_auth_missing_token", session_id=session_id)
        await websocket.close(code=WS_AUTH_CLOSE_CODE, reason="Missing token")
        return None

    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        sid = None

    async with async_session_factory() as db:
        principal = await principal_from_token(token, db)
        if principal is None:
            logger.warning("ws_auth_invalid_token", session_id=session_id)
            await websocket.close(code=WS_AUTH_CLOSE_CODE, reason="Invalid token")
            return None
        session = await db.get(InterviewSession, sid) if sid else None

    if session is None or not principal.can_access_session(session):
        logger.warning("ws_auth_session_denied", session_id=session_id, user_id=str(principal.user_id))
        await websocket.close(code=WS_AUTH_CLOSE_CODE, reason="Session not accessible")
        return None

    return principal.user_id
