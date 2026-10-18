"""Session creation (candidate details, meeting link) and idempotent pause/resume,
against the real app and Postgres.

The extension pauses a session when its Meet tab closes and resumes it when
capture restarts, using the session's pairing token, so both calls must be
safe to repeat.
"""

import uuid

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.dependencies import get_db
from src.main import app
from src.models.report import Report
from src.models.session import InterviewSession
from src.models.user import User
from src.schemas.session import normalize_meeting_url
from src.services.auth_service import AuthService


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark_db = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://meet.google.com/abc-defg-hij", "https://meet.google.com/abc-defg-hij"),
        ("meet.google.com/abc-defg-hij", "https://meet.google.com/abc-defg-hij"),
        ("  https://meet.google.com/ABC-DEFG-HIJ?authuser=1  ", "https://meet.google.com/abc-defg-hij"),
        ("https://meet.google.com/abc-defg-hij/", "https://meet.google.com/abc-defg-hij"),
        ("", None),
        (None, None),
    ],
)
def test_meeting_links_are_normalized(raw, expected) -> None:
    assert normalize_meeting_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://zoom.us/j/123456",
        "https://meet.google.com/",
        "https://meet.google.com/abc-defg",
        "http://meet.google.com/abc-defg-hij",
        "https://meet.google.com.evil.test/abc-defg-hij",
        "https://evil.test/?u=meet.google.com/abc-defg-hij",
    ],
)
def test_other_links_are_rejected(raw) -> None:
    with pytest.raises(ValueError):
        normalize_meeting_url(raw)


@pytest.fixture
async def world(monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", False)
    monkeypatch.setattr(settings, "environment", "production")
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    owner = User(id=uuid.uuid4(), email=f"owner-{uuid.uuid4().hex[:8]}@meet.test", name="owner", role="interviewer")
    async with factory() as db:
        db.add(owner)
        await db.commit()

    auth = AuthService(None)
    created: list[uuid.UUID] = []

    async def add_session(status: str, **fields) -> str:
        s = InterviewSession(
            id=uuid.uuid4(),
            user_id=owner.id,
            status=status,
            level="sde2",
            duration_minutes=30,
            focus_areas=[],
            **fields,
        )
        async with factory() as db:
            db.add(s)
            await db.commit()
        created.append(s.id)
        return str(s.id)

    async def status_of(session_id: str) -> str:
        async with factory() as db:
            return (await db.get(InterviewSession, uuid.UUID(session_id))).status

    yield {
        "owner_token": auth.create_access_token(str(owner.id)),
        "pairing_token": lambda sid: auth.create_pairing_token(str(owner.id), sid),
        "add_session": add_session,
        "status_of": status_of,
        "factory": factory,
        "created": created,
    }
    app.dependency_overrides.pop(get_db, None)
    async with factory() as db:
        await db.execute(delete(Report).where(Report.session_id.in_(created)))
        await db.execute(delete(InterviewSession).where(InterviewSession.id.in_(created)))
        await db.execute(delete(User).where(User.id == owner.id))
        await db.commit()
    await engine.dispose()


async def _call(method: str, path: str, token: str, json: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers={"Authorization": f"Bearer {token}"}, json=json)


@pytestmark_db
async def test_meeting_link_is_saved_on_create_and_returned(world) -> None:
    resp = await _call(
        "POST",
        "/api/sessions/start",
        world["owner_token"],
        {
            "level": "sde2",
            "duration_minutes": 30,
            "focus_areas": [],
            "meeting_url": "meet.google.com/abc-defg-hij?pli=1",
        },
    )
    assert resp.status_code == 200
    sid = resp.json()["session_id"]
    world["created"].append(uuid.UUID(sid))

    got = await _call("GET", f"/api/sessions/{sid}", world["owner_token"])
    assert got.json()["meeting_url"] == "https://meet.google.com/abc-defg-hij"

    listed = await _call("GET", "/api/history/sessions", world["owner_token"])
    row = next(s for s in listed.json()["sessions"] if s["id"] == sid)
    assert row["meeting_url"] == "https://meet.google.com/abc-defg-hij"


@pytestmark_db
async def test_candidate_details_are_saved_and_returned(world) -> None:
    """The dashboard sends the details prefilled from the resume, as edited."""
    resp = await _call(
        "POST",
        "/api/sessions/start",
        world["owner_token"],
        {
            "level": "sde2",
            "duration_minutes": 30,
            "focus_areas": ["coding"],
            "candidate_name": "  Jane Doe ",
            "candidate_email": "jane@example.com",
            "candidate_phone": "+1 555 0100",
            "candidate_role": "Backend Engineer",
            "resume_data": {"name": "Jane Doe", "email": "jane@example.com"},
        },
    )
    assert resp.status_code == 200
    sid = resp.json()["session_id"]
    world["created"].append(uuid.UUID(sid))

    got = (await _call("GET", f"/api/sessions/{sid}", world["owner_token"])).json()
    assert got["candidate_name"] == "Jane Doe"
    assert got["candidate_email"] == "jane@example.com"
    assert got["candidate_phone"] == "+1 555 0100"
    assert got["candidate_role"] == "Backend Engineer"
    assert got["status"] == "planning"

    listed = await _call("GET", "/api/history/sessions", world["owner_token"])
    row = next(s for s in listed.json()["sessions"] if s["id"] == sid)
    assert row["candidate_role"] == "Backend Engineer"


@pytestmark_db
async def test_guest_session_has_no_candidate_data_and_goes_live_without_a_plan(world) -> None:
    resp = await _call(
        "POST",
        "/api/sessions/start",
        world["owner_token"],
        {
            "level": "sde2",
            "duration_minutes": 30,
            "focus_areas": ["coding"],
            "candidate_name": "",
            "candidate_email": " ",
        },
    )
    sid = resp.json()["session_id"]
    world["created"].append(uuid.UUID(sid))

    live = await _call("POST", f"/api/sessions/{sid}/go-live", world["owner_token"])
    assert live.status_code == 200
    assert await world["status_of"](sid) == "live"

    got = (await _call("GET", f"/api/sessions/{sid}", world["owner_token"])).json()
    assert [got[k] for k in ("candidate_name", "candidate_email", "candidate_phone", "candidate_role")] == [None] * 4
    state = (await _call("GET", f"/api/sessions/{sid}/state", world["owner_token"])).json()
    assert state["plan"] is None


@pytestmark_db
async def test_create_rejects_a_non_meet_link(world) -> None:
    resp = await _call(
        "POST",
        "/api/sessions/start",
        world["owner_token"],
        {"level": "sde2", "duration_minutes": 30, "focus_areas": [], "meeting_url": "https://zoom.us/j/1"},
    )
    assert resp.status_code == 422


@pytestmark_db
async def test_meeting_link_can_be_set_and_cleared(world) -> None:
    sid = await world["add_session"]("live")
    path = f"/api/sessions/{sid}/meeting-url"

    set_resp = await _call("PATCH", path, world["owner_token"], {"meeting_url": "https://meet.google.com/xyz-abcd-efg"})
    assert set_resp.status_code == 200
    assert set_resp.json() == {"meeting_url": "https://meet.google.com/xyz-abcd-efg"}

    bad = await _call("PATCH", path, world["owner_token"], {"meeting_url": "https://example.test"})
    assert bad.status_code == 422

    cleared = await _call("PATCH", path, world["owner_token"], {"meeting_url": ""})
    assert cleared.json() == {"meeting_url": None}


@pytestmark_db
async def test_pause_and_resume_are_idempotent_with_the_pairing_token(world) -> None:
    """The extension's path: its pairing token, repeated calls, whatever state the dashboard left."""
    sid = await world["add_session"]("live")
    token = world["pairing_token"](sid)

    for _ in range(2):
        resp = await _call("POST", f"/api/sessions/{sid}/pause", token)
        assert resp.status_code == 200
        assert resp.json() == {"status": "paused"}
    assert await world["status_of"](sid) == "paused"

    for _ in range(2):
        resp = await _call("POST", f"/api/sessions/{sid}/resume", token)
        assert resp.status_code == 200
        assert resp.json() == {"status": "live"}
    assert await world["status_of"](sid) == "live"


@pytestmark_db
@pytest.mark.parametrize("status", ["planning", "ready", "ended"])
@pytest.mark.parametrize("action", ["pause", "resume"])
async def test_only_live_or_paused_sessions_change(world, status, action) -> None:
    sid = await world["add_session"](status)
    resp = await _call("POST", f"/api/sessions/{sid}/{action}", world["owner_token"])
    assert resp.status_code == 409
    assert await world["status_of"](sid) == status


@pytestmark_db
async def test_report_carries_the_meeting_link(world) -> None:
    sid = await world["add_session"]("ended", meeting_url="https://meet.google.com/abc-defg-hij")
    report = Report(
        id=uuid.uuid4(),
        session_id=uuid.UUID(sid),
        report_data={},
        recommendation="hire",
        confidence_level=0.5,
        is_final=True,
        status="completed",
    )
    async with world["factory"]() as db:
        db.add(report)
        await db.commit()

    resp = await _call("GET", f"/api/reports/{report.id}", world["owner_token"])
    assert resp.status_code == 200
    assert resp.json()["meeting_url"] == "https://meet.google.com/abc-defg-hij"


@pytestmark_db
async def test_pause_and_resume_tell_the_dashboard_once(world, monkeypatch) -> None:
    """A pause from the extension (Meet tab closed) must reach the open dashboard; a repeated call sends nothing."""
    from src.websocket.manager import dashboard_manager

    sent: list[tuple[str, dict]] = []

    async def capture(session_id: str, message: dict) -> None:
        sent.append((session_id, message))

    monkeypatch.setattr(dashboard_manager, "broadcast", capture)
    sid = await world["add_session"]("live")
    token = world["pairing_token"](sid)

    await _call("POST", f"/api/sessions/{sid}/pause", token)
    await _call("POST", f"/api/sessions/{sid}/pause", token)
    await _call("POST", f"/api/sessions/{sid}/resume", token)

    assert sent == [
        (sid, {"type": "phase_change", "payload": {"status": "paused"}}),
        (sid, {"type": "phase_change", "payload": {"status": "live"}}),
    ]


@pytestmark_db
async def test_ending_twice_returns_the_same_report(world, monkeypatch) -> None:
    """A second End (a double click while the first was slow) must not start a second report."""
    from sqlalchemy import func, select

    import src.api.sessions as sessions_api

    async def no_generation(**_kwargs) -> None:  # the LLM report run isn't under test
        return None

    monkeypatch.setattr(sessions_api, "_generate_report_background", no_generation)
    # No app startup under ASGITransport, so no checkpointer: this session has no checkpoints anyway.
    import src.workers.langgraph_session as langgraph_session

    async def no_checkpoint(*_args, **_kwargs):
        return None

    monkeypatch.setattr(langgraph_session, "load_checkpointed_session", no_checkpoint)
    monkeypatch.setattr(langgraph_session, "delete_session_threads", no_checkpoint)
    sid = await world["add_session"]("live")

    first = await _call("POST", f"/api/sessions/{sid}/end", world["owner_token"])
    second = await _call("POST", f"/api/sessions/{sid}/end", world["owner_token"])

    assert first.status_code == second.status_code == 200
    assert first.json()["report_id"]
    assert second.json() == first.json()
    async with world["factory"]() as db:
        count = await db.scalar(select(func.count()).select_from(Report).where(Report.session_id == uuid.UUID(sid)))
    assert count == 1
