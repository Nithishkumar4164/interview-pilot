"""Google sign-in, refresh and logout through the real app, Redis and Postgres.

Only the provider's HTTP endpoints are faked (tests/fake_oidc.py); state,
PKCE, ID-token verification, allowlist and user upsert all run for real.
"""

import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.api import auth as auth_api
from src.config import settings
from src.dependencies import get_db
from src.main import app
from src.models.user import User
from src.services.auth_service import AuthService
from src.services.oidc import OIDCClient
from src.utils import redis as redis_util
from tests.fake_oidc import FakeOIDCProvider


def _postgres_available() -> bool:
    try:
        with psycopg.connect(settings.database_url_sync, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable")


@pytest.fixture
async def env(monkeypatch):
    tag = uuid.uuid4().hex[:8]
    provider = FakeOIDCProvider(email=f"owner-{tag}@allowed.test", subject=f"sub-{tag}")
    for key, value in {
        "environment": "production",
        "dev_mode": False,
        "google_client_id": provider.client_id,
        "google_client_secret": "test-secret",
        "google_redirect_uri": "http://frontend.test/auth/callback",
        "oidc_issuer": provider.issuer,
        "oidc_authorization_url": f"{provider.issuer}/authorize",
        "oidc_token_url": f"{provider.issuer}/token",
        "oidc_jwks_url": f"{provider.issuer}/jwks",
        "allowed_login_domains": "allowed.test",
        "allowed_login_emails": "",
        "admin_emails": "",
    }.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(auth_api, "oidc_client", OIDCClient(http=httpx.AsyncClient(transport=provider.transport())))

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
    # https base URL so the Secure refresh cookie is sent back by the client.
    client = AsyncClient(transport=ASGITransport(app=app), base_url="https://frontend.test")
    yield provider, client, factory

    await client.aclose()
    app.dependency_overrides.pop(get_db, None)
    async with factory() as db:
        await db.execute(delete(User).where(User.email.like(f"%-{tag}@%")))
        await db.commit()
    await engine.dispose()
    await redis_util.get_redis().aclose()


async def _start(client: AsyncClient, next_path: str = "/reports") -> dict[str, str]:
    resp = await client.get("/api/auth/google/start", params={"next": next_path})
    assert resp.status_code == 200
    url = urlparse(resp.json()["authorization_url"])
    return {k: v[0] for k, v in parse_qs(url.query).items()}


async def _login(provider: FakeOIDCProvider, client: AsyncClient, next_path: str = "/reports") -> httpx.Response:
    query = await _start(client, next_path)
    redirect = urlparse(provider.authorize(query))  # the user approves on the provider's page
    params = {k: v[0] for k, v in parse_qs(redirect.query).items()}
    return await client.post("/api/auth/google/callback", json={"code": params["code"], "state": params["state"]})


async def test_login_issues_tokens_and_creates_user(env) -> None:
    provider, client, factory = env
    resp = await _login(provider, client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == provider.email
    assert body["user"]["role"] == "interviewer"
    assert body["next"] == "/reports"
    assert AuthService.decode_token(body["access_token"], ("access",))

    cookie = resp.headers["set-cookie"].lower()
    for attr in ("ip_refresh=", "httponly", "samesite=strict", "path=/api/auth", "secure"):
        assert attr in cookie, attr

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == provider.email

    async with factory() as db:
        user = (await db.execute(select(User).where(User.email == provider.email))).scalar_one()
    assert user.google_id == provider.subject


async def test_pkce_verifier_matches_the_challenge_sent_to_the_provider(env) -> None:
    provider, client, _ = env
    assert (await _login(provider, client)).status_code == 200
    # FakeOIDCProvider.token rejects a verifier that doesn't hash to the stored challenge.
    assert provider.token_requests[-1]["code_verifier"]
    assert provider.token_requests[-1]["grant_type"] == "authorization_code"


async def test_state_is_single_use(env) -> None:
    provider, client, _ = env
    query = await _start(client)
    params = {k: v[0] for k, v in parse_qs(urlparse(provider.authorize(query)).query).items()}
    first = await client.post("/api/auth/google/callback", json=params)
    replay = await client.post("/api/auth/google/callback", json=params)
    assert first.status_code == 200
    assert replay.status_code == 400


async def test_unknown_state_is_rejected(env) -> None:
    _, client, _ = env
    resp = await client.post("/api/auth/google/callback", json={"code": "x", "state": "never-issued"})
    assert resp.status_code == 400


@pytest.mark.parametrize(
    ("tamper", "foreign_key", "verified"),
    [
        ({"nonce": "attacker-nonce"}, False, True),
        ({"aud": "some-other-client"}, False, True),
        ({"iss": "https://evil.test"}, False, True),
        ({"exp": 1}, False, True),
        ({"at_hash": "AAAAAAAAAAAAAAAAAAAAAA"}, False, True),
        ({}, True, True),  # signed by a key not in the provider's JWKS
        ({}, False, False),  # email_verified=false
    ],
    ids=["nonce", "audience", "issuer", "expired", "at_hash", "foreign-key", "unverified-email"],
)
async def test_unverifiable_identity_is_rejected(env, tamper, foreign_key, verified) -> None:
    provider, client, factory = env
    provider.tamper, provider.sign_with_foreign_key, provider.email_verified = tamper, foreign_key, verified
    resp = await _login(provider, client)
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers
    async with factory() as db:
        assert (await db.execute(select(User).where(User.email == provider.email))).scalar_one_or_none() is None


async def test_email_outside_allowlist_is_refused_and_no_user_created(env) -> None:
    provider, client, factory = env
    provider.email = provider.email.replace("@allowed.test", "@outsider.test")
    resp = await _login(provider, client)
    assert resp.status_code == 403
    async with factory() as db:
        assert (await db.execute(select(User).where(User.email == provider.email))).scalar_one_or_none() is None


async def test_admin_emails_get_admin_role_on_login(env, monkeypatch) -> None:
    provider, client, _ = env
    monkeypatch.setattr(settings, "admin_emails", provider.email.upper())
    resp = await _login(provider, client)
    assert resp.json()["user"]["role"] == "admin"


async def test_existing_user_is_linked_by_verified_email(env) -> None:
    provider, client, factory = env
    async with factory() as db:
        db.add(User(id=uuid.uuid4(), email=provider.email, name="Pre-existing", role="interviewer"))
        await db.commit()
    resp = await _login(provider, client)
    assert resp.status_code == 200
    async with factory() as db:
        users = (await db.execute(select(User).where(User.email == provider.email))).scalars().all()
    assert len(users) == 1 and users[0].google_id == provider.subject


@pytest.mark.parametrize("next_path", ["//evil.test/x", "https://evil.test", "/\\evil.test", "reports"])
async def test_next_cannot_redirect_off_site(env, next_path) -> None:
    provider, client, _ = env
    resp = await _login(provider, client, next_path)
    assert resp.json()["next"] == "/"


async def test_refresh_uses_the_cookie_and_rotates_it(env) -> None:
    provider, client, _ = env
    login = await _login(provider, client)
    first_cookie = client.cookies.get("ip_refresh")
    assert first_cookie

    refreshed = await client.post("/api/auth/refresh")
    assert refreshed.status_code == 200
    assert AuthService.decode_token(refreshed.json()["access_token"], ("access",))
    assert refreshed.json()["user"]["id"] == login.json()["user"]["id"]
    assert "ip_refresh=" in refreshed.headers["set-cookie"]


async def test_refresh_without_cookie_is_401(env) -> None:
    _, client, _ = env
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_access_token_is_not_accepted_as_refresh_token(env) -> None:
    provider, client, _ = env
    access = (await _login(provider, client)).json()["access_token"]
    client.cookies.clear()
    client.cookies.set("ip_refresh", access, domain="frontend.test", path="/api/auth")
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 401
    # The unusable cookie is cleared so the browser stops sending it.
    assert 'ip_refresh=""' in resp.headers["set-cookie"] and "max-age=0" in resp.headers["set-cookie"].lower()


async def test_logout_clears_the_refresh_cookie(env) -> None:
    provider, client, _ = env
    await _login(provider, client)
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 204
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_start_refuses_when_google_is_not_configured(env, monkeypatch) -> None:
    _, client, _ = env
    monkeypatch.setattr(settings, "google_client_id", "")
    assert (await client.get("/api/auth/google/start")).status_code == 503
