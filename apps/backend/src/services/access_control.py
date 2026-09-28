"""Who is calling, and may they touch this session.

Framework-free so REST dependencies and WebSocket handshakes share one rule.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.session import InterviewSession
from src.services.auth_service import ROLE_ADMIN, ROLE_INTERVIEWER, AuthService, TokenType

# UUID of the seeded local dev user (matches migrations/seeds).
DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    role: str
    # Set for pairing tokens: the one session this credential may act on.
    session_scope: uuid.UUID | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def can_access_session(self, session: InterviewSession) -> bool:
        if self.session_scope is not None and session.id != self.session_scope:
            return False
        return self.is_admin or session.user_id == self.user_id


def dev_mode_active() -> bool:
    # The settings validator guarantees dev_mode is False outside development.
    return settings.dev_mode and settings.environment == "development"


def dev_principal() -> Principal:
    return Principal(user_id=DEV_USER_ID, role=ROLE_INTERVIEWER)


async def principal_from_token(
    token: str,
    db: AsyncSession,
    allowed_types: tuple[TokenType, ...] = ("access", "pairing"),
) -> Principal | None:
    """Resolve a bearer/query token to a Principal, or None if invalid.

    The user row is loaded on every call so role changes and deleted users
    take effect immediately rather than when the token expires.
    """
    claims = AuthService.decode_token(token, allowed_types)
    if claims is None:
        return None
    user = await AuthService(db).get_user_by_id(claims["sub"])
    if user is None:
        return None
    scope: uuid.UUID | None = None
    if claims["type"] == "pairing":
        try:
            scope = uuid.UUID(claims["sid"])
        except ValueError:
            return None
    return Principal(user_id=user.id, role=user.role, session_scope=scope)
