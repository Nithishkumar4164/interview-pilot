import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.dependencies import get_db
from src.models.user import User
from src.schemas.auth import TokenRefreshRequest, TokenResponse, UserResponse
from src.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

# UUID of the seeded local dev user (matches migrations/seeds).
DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency that extracts and validates the current user from JWT."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    auth_service = AuthService(db)
    user_id = auth_service.verify_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await auth_service.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


async def get_request_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the current request user.

    In development with ``dev_mode=True`` this short-circuits to a
    transient ``User`` populated with the seeded dev UUID — no DB query —
    so handlers that only read ``user.id`` avoid paying for an extra
    round-trip on every request. Any other environment falls through to
    :func:`get_current_user`, which enforces a valid bearer token.

    The :class:`Settings` post-init validator already guarantees
    ``dev_mode`` cannot be ``True`` when ``environment != "development"``.
    """
    if settings.dev_mode and settings.environment == "development":
        stub = User()
        stub.id = DEV_USER_ID
        stub.email = "dev@interview-pilot.local"
        stub.name = "Local Dev"
        stub.role = "interviewer"
        stub.google_id = None
        stub.avatar_url = None
        return stub

    return await get_current_user(credentials=credentials, db=db)


def require_role(*roles: str):
    """Dependency factory that checks if the current user has one of the required roles."""

    async def check_role(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' not authorized. Required: {roles}",
            )
        return user

    return check_role


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: TokenRefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    auth_service = AuthService(db)
    user_id = auth_service.verify_token(body.refresh_token, expected_type="refresh")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    return TokenResponse(
        access_token=auth_service.create_access_token(user_id),
        refresh_token=auth_service.create_refresh_token(user_id),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        avatar_url=user.avatar_url,
    )
