"""Sign-in (Google OIDC), token refresh and logout.

The access token is returned in the body and kept in memory by the client.
The refresh token only ever travels in an HttpOnly, SameSite=Strict cookie
scoped to /api/auth, so page scripts cannot read it and cross-site requests
cannot use it.
"""

import json

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.authz import get_user_principal
from src.config import settings
from src.dependencies import get_db
from src.models.user import User
from src.schemas.auth import AuthSessionResponse, GoogleCallbackRequest, GoogleStartResponse, UserResponse
from src.services.access_control import Principal
from src.services.auth_service import AuthService, login_allowed
from src.services.oidc import OIDCError, oidc_client
from src.utils.logging import get_logger
from src.utils.redis import get_redis

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger("auth")

REFRESH_COOKIE = "ip_refresh"
REFRESH_COOKIE_PATH = "/api/auth"
OIDC_STATE_PREFIX = "oidc_state:"
OIDC_STATE_TTL_S = 600


def _safe_next(next_path: str | None) -> str:
    """Only same-origin absolute paths, so the callback cannot be an open redirect."""
    if not next_path or not next_path.startswith("/") or next_path.startswith("//") or "\\" in next_path:
        return "/"
    return next_path


def _user_response(user: User) -> UserResponse:
    return UserResponse(id=str(user.id), email=user.email, name=user.name, role=user.role, avatar_url=user.avatar_url)


def _issue_session(response: Response, auth: AuthService, user: User, next_path: str = "/") -> AuthSessionResponse:
    user_id = str(user.id)
    response.set_cookie(
        REFRESH_COOKIE,
        auth.create_refresh_token(user_id),
        max_age=settings.jwt_refresh_token_expire_days * 86400,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
    )
    return AuthSessionResponse(
        access_token=auth.create_access_token(user_id),
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=_user_response(user),
        next=next_path,
    )


@router.get("/google/start", response_model=GoogleStartResponse)
async def google_start(next: str | None = Query(None)) -> GoogleStartResponse:
    if not settings.google_client_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google sign-in is not configured")
    request = oidc_client.authorization_request()
    await get_redis().set(
        f"{OIDC_STATE_PREFIX}{request.state}",
        json.dumps({"nonce": request.nonce, "code_verifier": request.code_verifier, "next": _safe_next(next)}),
        ex=OIDC_STATE_TTL_S,
    )
    return GoogleStartResponse(authorization_url=request.url)


@router.post("/google/callback", response_model=AuthSessionResponse)
async def google_callback(
    body: GoogleCallbackRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthSessionResponse:
    # GETDEL: each state is usable exactly once.
    raw = await get_redis().getdel(f"{OIDC_STATE_PREFIX}{body.state}")
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Sign-in expired or already used; try again"
        )
    pending = json.loads(raw)

    try:
        identity = await oidc_client.exchange_code(body.code, pending["code_verifier"], pending["nonce"])
    except OIDCError as e:
        logger.warning("oidc_login_failed", reason=str(e))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign-in failed") from None

    if not login_allowed(identity.email):
        logger.warning("oidc_login_not_allowed", email_domain=identity.email.rsplit("@", 1)[-1])
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is not allowed to sign in")

    auth = AuthService(db)
    user = await auth.upsert_google_user(identity)
    await db.commit()
    logger.info("oidc_login", user_id=str(user.id), role=user.role)
    return _issue_session(response, auth, user, pending["next"])


@router.post("/refresh", response_model=AuthSessionResponse)
async def refresh(
    response: Response,
    refresh_token: str | None = Cookie(None, alias=REFRESH_COOKIE),
    db: AsyncSession = Depends(get_db),
) -> AuthSessionResponse | JSONResponse:
    auth = AuthService(db)
    user_id = auth.verify_token(refresh_token, expected_type="refresh") if refresh_token else None
    user = await auth.get_user_by_id(user_id) if user_id else None
    if user is None:
        # Returned, not raised: headers set on ``response`` are dropped when an
        # HTTPException is raised, and a stale cookie should be cleared.
        denied = JSONResponse({"detail": "Not signed in"}, status_code=status.HTTP_401_UNAUTHORIZED)
        denied.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
        return denied
    return _issue_session(response, auth, user)  # rotates the refresh cookie


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserResponse)
async def get_me(
    principal: Principal = Depends(get_user_principal),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    user = await db.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    return _user_response(user)
