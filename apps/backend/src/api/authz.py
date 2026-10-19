"""FastAPI dependencies for authentication and per-resource authorization.

Resource dependencies return the loaded resource or raise 404 — never 403 —
so a caller cannot probe which session/plan/report IDs exist.
"""

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.models.report import Report
from src.models.session import InterviewSession
from src.services.access_control import Principal, dev_mode_active, dev_principal, principal_from_token

bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _not_found(kind: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{kind} not found")


def _parse_uuid(value: str, kind: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise _not_found(kind) from None


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """Any valid credential: an access token, or a session-scoped pairing token.

    A presented token is always validated, even in dev mode, so login can be
    exercised locally; with no token, dev mode falls back to the seeded dev user.
    """
    if credentials is None:
        if dev_mode_active():
            return dev_principal()
        raise _unauthorized("Missing bearer token")
    principal = await principal_from_token(credentials.credentials, db)
    if principal is None:
        raise _unauthorized("Invalid or expired token")
    return principal


async def get_user_principal(principal: Principal = Depends(get_principal)) -> Principal:
    """A signed-in user; pairing tokens (one-session capabilities) are refused."""
    if principal.session_scope is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A session pairing token cannot be used for this endpoint",
        )
    return principal


async def authorized_session(
    session_id: str,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
) -> InterviewSession:
    session = await db.get(InterviewSession, _parse_uuid(session_id, "Session"))
    if session is None or not principal.can_access_session(session):
        raise _not_found("Session")
    return session


async def authorized_report(
    report_id: str,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
) -> Report:
    report = await db.get(Report, _parse_uuid(report_id, "Report"))
    if report is None:
        raise _not_found("Report")
    session = await db.get(InterviewSession, report.session_id)
    if session is None or not principal.can_access_session(session):
        raise _not_found("Report")
    return report


async def require_session_access(
    session_id: str,
    principal: Principal,
    db: AsyncSession,
) -> InterviewSession:
    """Non-dependency form, for handlers that receive a session id in the body."""
    return await authorized_session(session_id, principal, db)
