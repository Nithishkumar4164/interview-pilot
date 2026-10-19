import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.user import User
from src.services.oidc import VerifiedIdentity

TokenType = Literal["access", "refresh", "pairing"]

ROLE_ADMIN = "admin"
ROLE_INTERVIEWER = "interviewer"


def login_allowed(email: str) -> bool:
    """Whether a provider-verified email may sign in.

    Development with no allowlist configured allows everyone; the settings
    validator forbids an empty allowlist in any other environment.
    """
    email = email.lower()
    emails, domains = settings.allowed_login_email_set, settings.allowed_login_domain_set
    if not emails and not domains:
        return settings.environment == "development"
    return email in emails or email.rsplit("@", 1)[-1] in domains


def role_for(email: str) -> str:
    return ROLE_ADMIN if email.lower() in settings.admin_email_set else ROLE_INTERVIEWER


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Tokens ────────────────────────────────────────────────────────

    @staticmethod
    def _encode(claims: dict[str, Any], lifetime: timedelta) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {**claims, "iat": now, "exp": now + lifetime},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

    def create_access_token(self, user_id: str) -> str:
        return self._encode(
            {"sub": user_id, "type": "access"},
            timedelta(minutes=settings.jwt_access_token_expire_minutes),
        )

    def create_refresh_token(self, user_id: str) -> str:
        return self._encode(
            {"sub": user_id, "type": "refresh"},
            timedelta(days=settings.jwt_refresh_token_expire_days),
        )

    def create_pairing_token(self, user_id: str, session_id: str) -> str:
        """A credential usable only for one session's routes and sockets."""
        return self._encode(
            {"sub": user_id, "type": "pairing", "sid": session_id},
            timedelta(hours=settings.pairing_token_expire_hours),
        )

    @staticmethod
    def decode_token(token: str, allowed_types: tuple[TokenType, ...]) -> dict[str, Any] | None:
        """Return the claims of a valid token whose type is allowed, else None."""
        try:
            claims = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        except JWTError:
            return None
        if claims.get("type") not in allowed_types or not claims.get("sub"):
            return None
        if claims["type"] == "pairing" and not claims.get("sid"):
            return None
        return claims

    def verify_token(self, token: str, expected_type: TokenType = "access") -> str | None:
        """Returns user_id if token is valid, None otherwise."""
        claims = self.decode_token(token, (expected_type,))
        return claims["sub"] if claims else None

    # ── Users ─────────────────────────────────────────────────────────

    async def upsert_google_user(self, identity: VerifiedIdentity) -> User:
        """Find by Google subject, else link by verified email, else create.

        Profile fields and role are refreshed on every login so changes to
        ADMIN_EMAILS take effect at the next sign-in.
        """
        user = (await self.db.execute(select(User).where(User.google_id == identity.subject))).scalar_one_or_none()
        if user is None:
            user = (await self.db.execute(select(User).where(User.email == identity.email))).scalar_one_or_none()
        if user is None:
            user = User(id=uuid.uuid4(), email=identity.email)
            self.db.add(user)

        user.google_id = identity.subject
        user.email = identity.email
        user.name = identity.name
        user.avatar_url = identity.picture
        user.role = role_for(identity.email)
        await self.db.flush()
        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        try:
            uid = uuid.UUID(user_id)
        except ValueError:
            return None
        result = await self.db.execute(select(User).where(User.id == uid))
        return result.scalar_one_or_none()
