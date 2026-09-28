"""OpenID Connect client for Google sign-in (authorization code + PKCE).

Endpoints come from settings (defaulting to Google) so tests and local
end-to-end runs can use a stand-in provider. The HTTP client is injectable
for the same reason.
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("oidc")

JWKS_CACHE_TTL_S = 3600


class OIDCError(Exception):
    """The provider rejected the exchange or returned an unverifiable identity."""


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str
    state: str
    nonce: str
    code_verifier: str


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    email: str
    name: str
    picture: str | None


def _accepted_issuers() -> tuple[str, ...]:
    # Google signs ID tokens with either form of its issuer.
    if settings.oidc_issuer == "https://accounts.google.com":
        return ("https://accounts.google.com", "accounts.google.com")
    return (settings.oidc_issuer,)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class OIDCClient:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._jwks: dict[str, Any] | None = None
        self._jwks_fetched_at = 0.0

    def authorization_request(self) -> AuthorizationRequest:
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        query = urlencode(
            {
                "client_id": settings.google_client_id,
                "redirect_uri": settings.google_redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return AuthorizationRequest(
            url=f"{settings.oidc_authorization_url}?{query}",
            state=state,
            nonce=nonce,
            code_verifier=verifier,
        )

    async def exchange_code(self, code: str, code_verifier: str, expected_nonce: str) -> VerifiedIdentity:
        try:
            resp = await self._http.post(
                settings.oidc_token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": settings.google_redirect_uri,
                },
            )
        except httpx.HTTPError as e:
            raise OIDCError(f"token endpoint unreachable: {type(e).__name__}") from e
        if resp.status_code != 200:
            # Provider error bodies are safe to log (no tokens on failure).
            raise OIDCError(f"token exchange failed: HTTP {resp.status_code} {resp.text[:200]}")

        tokens = resp.json()
        id_token = tokens.get("id_token")
        if not id_token:
            raise OIDCError("token response has no id_token")
        claims = await self._verify_id_token(id_token, tokens.get("access_token"))

        if claims.get("nonce") != expected_nonce:
            raise OIDCError("nonce mismatch")
        if claims.get("email_verified") is not True:
            raise OIDCError("email not verified by provider")
        email = claims.get("email")
        if not email:
            raise OIDCError("id_token has no email")

        return VerifiedIdentity(
            subject=str(claims["sub"]),
            email=email.lower(),
            name=claims.get("name") or email,
            picture=claims.get("picture"),
        )

    async def _verify_id_token(self, id_token: str, access_token: str | None) -> dict[str, Any]:
        last_error: JWTError | None = None
        for force_refresh in (False, True):  # retry once with fresh keys (provider key rotation)
            jwks = await self._get_jwks(force_refresh=force_refresh)
            try:
                return jwt.decode(
                    id_token,
                    jwks,
                    algorithms=["RS256"],
                    audience=settings.google_client_id,
                    issuer=_accepted_issuers(),
                    access_token=access_token,
                )
            except JWTError as e:
                last_error = e
        raise OIDCError(f"id_token verification failed: {last_error}")

    async def _get_jwks(self, *, force_refresh: bool) -> dict[str, Any]:
        fresh = time.monotonic() - self._jwks_fetched_at < JWKS_CACHE_TTL_S
        if self._jwks is not None and fresh and not force_refresh:
            return self._jwks
        try:
            resp = await self._http.get(settings.oidc_jwks_url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise OIDCError(f"JWKS fetch failed: {type(e).__name__}") from e
        self._jwks = resp.json()
        self._jwks_fetched_at = time.monotonic()
        return self._jwks


oidc_client = OIDCClient()
