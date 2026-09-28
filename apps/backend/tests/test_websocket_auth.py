"""WebSocket upgrade authentication against real tokens and a real Postgres."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.models.session import InterviewSession
from src.models.user import User
from src.services.access_control import DEV_USER_ID
from src.services.auth_service import AuthService
from src.websocket import auth as ws_auth
from src.websocket.auth import authenticate_ws


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


def _fake_ws(token: str | None = None) -> MagicMock:
    ws = MagicMock()
    ws.query_params = {"token": token} if token else {}
    ws.headers = {}
    ws.close = AsyncMock()
    ws.accept = AsyncMock()
    return ws


async def test_dev_mode_without_token_is_the_dev_user() -> None:
    # Default Settings has dev_mode=True + environment="development"
    ws = _fake_ws()
    assert await authenticate_ws(ws, "any-session-id") == DEV_USER_ID
    ws.close.assert_not_called()


@pytest.fixture
def production_mode(monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", False)
    monkeypatch.setattr(settings, "environment", "production")


async def test_missing_token_is_refused_in_production(production_mode) -> None:
    ws = _fake_ws(token=None)
    assert await authenticate_ws(ws, "any-session-id") is None
    assert ws.close.await_args.kwargs["code"] == ws_auth.WS_AUTH_CLOSE_CODE


@pytest.fixture
async def world(production_mode, monkeypatch):
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ws_auth, "async_session_factory", factory)
    tag = uuid.uuid4().hex[:8]
    owner, other, admin = (
        User(id=uuid.uuid4(), email=f"{n}-{tag}@ws.test", name=n, role=r)
        for n, r in (("owner", "interviewer"), ("other", "interviewer"), ("admin", "admin"))
    )
    s1, s2 = (
        InterviewSession(
            id=uuid.uuid4(), user_id=owner.id, status="live", level="mid", duration_minutes=30, focus_areas=[]
        )
        for _ in range(2)
    )
    async with factory() as db:
        db.add_all([owner, other, admin])
        await db.flush()
        db.add_all([s1, s2])
        await db.commit()
    auth = AuthService(None)
    yield {
        "s1": str(s1.id),
        "s2": str(s2.id),
        "owner": auth.create_access_token(str(owner.id)),
        "other": auth.create_access_token(str(other.id)),
        "admin": auth.create_access_token(str(admin.id)),
        "pairing_s1": auth.create_pairing_token(str(owner.id), str(s1.id)),
        "refresh": auth.create_refresh_token(str(owner.id)),
        "owner_id": owner.id,
    }
    async with factory() as db:
        await db.execute(delete(InterviewSession).where(InterviewSession.id.in_([s1.id, s2.id])))
        await db.execute(delete(User).where(User.id.in_([owner.id, other.id, admin.id])))
        await db.commit()
    await engine.dispose()


@pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")
@pytest.mark.parametrize(
    ("token_key", "session_key", "allowed"),
    [
        ("owner", "s1", True),
        ("admin", "s1", True),
        ("pairing_s1", "s1", True),
        ("pairing_s1", "s2", False),  # owner's other session: scope must stop it
        ("other", "s1", False),
        ("refresh", "s1", False),  # wrong token type
        ("garbage", "s1", False),
        ("owner", "not-a-uuid", False),
    ],
)
async def test_handshake(world, token_key, session_key, allowed) -> None:
    ws = _fake_ws(world.get(token_key, "not-a-jwt"))
    session_id = world.get(session_key, session_key)
    result = await authenticate_ws(ws, session_id)
    if allowed:
        assert result is not None
        ws.close.assert_not_called()
    else:
        assert result is None
        assert ws.close.await_args.kwargs["code"] == ws_auth.WS_AUTH_CLOSE_CODE
