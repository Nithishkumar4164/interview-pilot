"""Authorization matrix against the real app and a real Postgres, in production mode.

Principals: the session owner, another interviewer, an admin, a pairing token
for the owner's session, a pairing token for a different session, and no
credentials at all.
"""

import uuid

import psycopg
import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.dependencies import get_db
from src.main import app
from src.models.plan import InterviewPlan
from src.models.report import Report, ReportNote
from src.models.session import InterviewSession
from src.models.user import User
from src.services.auth_service import AuthService


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


PUBLIC_PATHS = {
    "/health",
    "/health/providers",
    "/api/status",
    "/api/auth/google/start",
    "/api/auth/google/callback",
    "/api/auth/refresh",
    "/api/auth/logout",
}


def _dependency_names(dependant, acc: set[str]) -> set[str]:
    for sub in dependant.dependencies:
        acc.add(getattr(sub.call, "__name__", ""))
        _dependency_names(sub, acc)
    return acc


def test_every_non_public_route_requires_a_principal() -> None:
    """A route added without auth fails here, not in production."""
    unguarded = [
        f"{','.join(sorted(r.methods))} {r.path}"
        for r in app.routes
        if isinstance(r, APIRoute)
        and r.path not in PUBLIC_PATHS
        and "get_principal" not in _dependency_names(r.dependant, set())
    ]
    assert unguarded == []


pytestmark_db = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@pytest.fixture
def production_mode(monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", False)
    monkeypatch.setattr(settings, "environment", "production")


@pytest.fixture
async def world(production_mode):
    """Two sessions (owner's and other's), a plan and a report on the owner's; tokens for each principal."""
    # NullPool: the app's global pool would bind connections to the first
    # test's event loop; each test gets its own loop.
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
    tag = uuid.uuid4().hex[:8]
    users = {
        name: User(id=uuid.uuid4(), email=f"{name}-{tag}@authz.test", name=name, role=role)
        for name, role in (("owner", "interviewer"), ("other", "interviewer"), ("admin", "admin"))
    }
    s_owner = InterviewSession(
        id=uuid.uuid4(), user_id=users["owner"].id, status="ended", level="mid", duration_minutes=30, focus_areas=[]
    )
    s_other = InterviewSession(
        id=uuid.uuid4(), user_id=users["other"].id, status="planning", level="mid", duration_minutes=30, focus_areas=[]
    )
    # A second session of the same owner: only the pairing scope keeps a token for s_owner out of it.
    s_owner_2 = InterviewSession(
        id=uuid.uuid4(), user_id=users["owner"].id, status="planning", level="mid", duration_minutes=30, focus_areas=[]
    )
    plan = InterviewPlan(id=uuid.uuid4(), session_id=s_owner.id, plan_data={"phases": []}, version=1, is_active=True)
    report = Report(
        id=uuid.uuid4(),
        session_id=s_owner.id,
        report_data={},
        recommendation="hire",
        confidence_level=0.5,
        is_final=True,
        status="completed",
    )
    async with factory() as db:
        db.add_all(list(users.values()))
        await db.flush()
        db.add_all([s_owner, s_other, s_owner_2])
        await db.flush()
        db.add_all([plan, report])
        await db.commit()

    auth = AuthService(None)  # token helpers don't touch the DB
    tokens = {
        "owner": auth.create_access_token(str(users["owner"].id)),
        "other": auth.create_access_token(str(users["other"].id)),
        "admin": auth.create_access_token(str(users["admin"].id)),
        "pairing_owner_session": auth.create_pairing_token(str(users["owner"].id), str(s_owner.id)),
        "pairing_other_session": auth.create_pairing_token(str(users["other"].id), str(s_other.id)),
        "pairing_owner_session_by_admin": auth.create_pairing_token(str(users["admin"].id), str(s_owner.id)),
        "none": None,
    }
    yield {
        "s": str(s_owner.id),
        "s_other": str(s_other.id),
        "s_owner_2": str(s_owner_2.id),
        "plan": str(plan.id),
        "report": str(report.id),
        "tokens": tokens,
    }
    app.dependency_overrides.pop(get_db, None)

    async with factory() as db:
        await db.execute(delete(ReportNote).where(ReportNote.report_id == report.id))
        await db.execute(delete(Report).where(Report.id == report.id))
        await db.execute(delete(InterviewPlan).where(InterviewPlan.id == plan.id))
        await db.execute(
            delete(InterviewSession).where(InterviewSession.id.in_([s_owner.id, s_other.id, s_owner_2.id]))
        )
        await db.execute(delete(User).where(User.id.in_([u.id for u in users.values()])))
        await db.commit()
    await engine.dispose()


async def _call(method: str, path: str, token: str | None, json: dict | None = None) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.request(method, path, headers=headers, json=json)
    return resp.status_code


# (method, path template, body) — read-only or idempotent calls on the owner's resources.
SESSION_SCOPED = [
    ("GET", "/api/sessions/{s}", None),
    ("GET", "/api/sessions/{s}/state", None),
    ("GET", "/api/history/sessions/{s}/detail", None),
    ("GET", "/api/history/sessions/{s}/report", None),
    ("GET", "/api/plans/{plan}", None),
    ("GET", "/api/reports/{report}", None),
    ("GET", "/api/reports/{report}/status", None),
    ("POST", "/api/sessions/{s}/register-plan-hints", {"phases": []}),
]

# Principal → expected status on SESSION_SCOPED routes.
EXPECTED = {
    "owner": 200,
    "admin": 200,
    "pairing_owner_session": 200,
    "other": 404,  # not 403: existence is not revealed
    "pairing_other_session": 404,
    "none": 401,
}


@pytestmark_db
@pytest.mark.parametrize("principal", list(EXPECTED))
@pytest.mark.parametrize(("method", "template", "body"), SESSION_SCOPED, ids=lambda x: str(x))
async def test_session_scoped_routes(world, principal, method, template, body) -> None:
    path = template.format(**world)
    assert await _call(method, path, world["tokens"][principal], body) == EXPECTED[principal]


@pytestmark_db
@pytest.mark.parametrize(
    ("method", "template", "body"),
    [
        ("POST", "/api/sessions/{s}/hints/next", {"question_id": "q"}),
        ("POST", "/api/sessions/{s}/pause", None),
        ("POST", "/api/sessions/{s}/resume", None),
        ("PATCH", "/api/sessions/{s}/meeting-url", {"meeting_url": None}),
        ("POST", "/api/sessions/{s}/go-live", None),
        ("POST", "/api/sessions/{s}/end", None),
        ("DELETE", "/api/sessions/{s}", None),
        ("POST", "/api/reports/generate/{s}", None),
        ("POST", "/api/reports/{report}/notes", {"note_text": "x"}),
        ("PATCH", "/api/plans/{plan}/remove", {"phase_index": 0, "question_index": 0}),
        (
            "POST",
            "/api/plans/create",
            {"session_id": "{s}", "level": "sde2", "duration_minutes": 30, "focus_areas": []},
        ),
    ],
    ids=lambda x: str(x),
)
async def test_mutations_are_refused_for_other_users(world, method, template, body) -> None:
    path = template.format(**world)
    if body:
        body = {k: v.format(**world) if isinstance(v, str) else v for k, v in body.items()}
    assert await _call(method, path, world["tokens"]["other"], body) == 404
    assert await _call(method, path, None, body) == 401


@pytestmark_db
async def test_malformed_ids_are_404_not_500(world) -> None:
    token = world["tokens"]["owner"]
    for path in ("/api/sessions/not-a-uuid", "/api/plans/not-a-uuid", "/api/reports/not-a-uuid"):
        assert await _call("GET", path, token) == 404


@pytestmark_db
async def test_pairing_tokens_are_refused_outside_their_session(world) -> None:
    pairing = world["tokens"]["pairing_owner_session"]
    # User-level endpoints: a one-session capability must not act as the user.
    for method, path in (
        ("GET", "/api/reports/"),
        ("GET", "/api/history/sessions"),
        ("GET", "/api/auth/me"),
        ("POST", f"/api/sessions/{world['s']}/pairing-token"),
    ):
        assert await _call(method, path, pairing) == 403, path
    # Another session: indistinguishable from missing.
    assert await _call("GET", f"/api/sessions/{world['s_other']}", pairing) == 404


@pytestmark_db
async def test_lists_are_scoped_to_the_caller(world) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

        async def ids(token: str, path: str, key: str) -> set[str]:
            resp = await client.get(path, headers={"Authorization": f"Bearer {token}"}, params={"limit": 100})
            assert resp.status_code == 200
            return {row["id"] for row in resp.json()[key]}

        owner_reports = await ids(world["tokens"]["owner"], "/api/reports/", "reports")
        other_reports = await ids(world["tokens"]["other"], "/api/reports/", "reports")
        other_sessions = await ids(world["tokens"]["other"], "/api/history/sessions", "sessions")

    assert world["report"] in owner_reports
    assert world["report"] not in other_reports
    assert world["s"] not in other_sessions
    assert world["s_other"] in other_sessions


@pytestmark_db
async def test_compare_refuses_reports_the_caller_cannot_see(world) -> None:
    ids = f"{world['report']},{uuid.uuid4()}"
    assert await _call("GET", f"/api/reports/compare?ids={ids}", world["tokens"]["other"]) == 404


@pytestmark_db
async def test_pairing_token_mint_is_scoped_to_the_session(world) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/sessions/{world['s']}/pairing-token",
            headers={"Authorization": f"Bearer {world['tokens']['owner']}"},
        )
    assert resp.status_code == 200
    token = resp.json()["pairing_token"]
    claims = AuthService.decode_token(token, ("pairing",))
    assert claims and claims["sid"] == world["s"]
    assert await _call("GET", f"/api/sessions/{world['s']}", token) == 200
    assert await _call("GET", f"/api/sessions/{world['s_other']}", token) == 404


@pytestmark_db
async def test_pairing_token_cannot_reach_its_owners_other_sessions(world) -> None:
    """The user behind the token owns s_owner_2 (or is admin); the token's scope must still stop it."""
    for key in ("pairing_owner_session", "pairing_owner_session_by_admin"):
        token = world["tokens"][key]
        assert await _call("GET", f"/api/sessions/{world['s']}", token) == 200, key
        assert await _call("GET", f"/api/sessions/{world['s_owner_2']}", token) == 404, key
