import uuid
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.user import User


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def create_access_token(self, user_id: str) -> str:
        expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
        return jwt.encode(
            {"sub": user_id, "exp": expire, "type": "access"},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

    def create_refresh_token(self, user_id: str) -> str:
        expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expire_days)
        return jwt.encode(
            {"sub": user_id, "exp": expire, "type": "refresh"},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

    def verify_token(self, token: str, expected_type: str = "access") -> str | None:
        """Returns user_id if token is valid, None otherwise."""
        try:
            payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            if payload.get("type") != expected_type:
                return None
            return payload.get("sub")
        except JWTError:
            return None

    async def get_or_create_google_user(
        self, email: str, name: str, google_id: str, avatar_url: str | None = None
    ) -> User:
        result = await self.db.execute(select(User).where(User.google_id == google_id))
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                id=uuid.uuid4(),
                email=email,
                name=name,
                google_id=google_id,
                avatar_url=avatar_url,
                role="interviewer",
            )
            self.db.add(user)
            await self.db.flush()

        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        result = await self.db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        return result.scalar_one_or_none()
