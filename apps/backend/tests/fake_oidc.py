"""A stand-in OpenID provider for tests and local end-to-end runs.

In-process: ``FakeOIDCProvider.transport()`` returns an httpx MockTransport
serving the token and JWKS endpoints.

As a server (for browser end-to-end runs):
    uv run python -m tests.fake_oidc --port 9400 --email you@example.com
then point the backend at it:
    INTERVIEW_PILOT_OIDC_ISSUER=http://localhost:9400
    INTERVIEW_PILOT_OIDC_AUTHORIZATION_URL=http://localhost:9400/authorize
    INTERVIEW_PILOT_OIDC_TOKEN_URL=http://localhost:9400/token
    INTERVIEW_PILOT_OIDC_JWKS_URL=http://localhost:9400/jwks
/authorize auto-approves and redirects back with a code, like a user who
clicked "Continue" on the consent screen.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

KID = "fake-key-1"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def at_hash(access_token: str) -> str:
    digest = hashlib.sha256(access_token.encode()).digest()
    return _b64url(digest[: len(digest) // 2])


@dataclass
class PendingCode:
    client_id: str
    redirect_uri: str
    nonce: str
    code_challenge: str
    claims: dict


@dataclass
class FakeOIDCProvider:
    issuer: str = "https://fake-idp.test"
    client_id: str = "test-client-id"
    email: str = "owner@example.com"
    name: str = "Test User"
    subject: str = "google-sub-123"
    email_verified: bool = True
    # Overrides applied to the next minted id_token (for negative tests).
    tamper: dict = field(default_factory=dict)
    sign_with_foreign_key: bool = False
    codes: dict[str, PendingCode] = field(default_factory=dict)
    token_requests: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._foreign_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # ── keys ──────────────────────────────────────────────────────────
    def _pem(self, key) -> bytes:
        return key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )

    def jwks(self) -> dict:
        public_pem = self._key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        key = jwk.construct(public_pem, algorithm="RS256").to_dict()
        key.update({"kid": KID, "use": "sig", "alg": "RS256"})
        return {"keys": [key]}

    # ── flow ──────────────────────────────────────────────────────────
    def authorize(self, query: dict[str, str]) -> str:
        """Approve an authorization request; return the redirect URL back to the client."""
        code = secrets.token_urlsafe(16)
        self.codes[code] = PendingCode(
            client_id=query["client_id"],
            redirect_uri=query["redirect_uri"],
            nonce=query["nonce"],
            code_challenge=query["code_challenge"],
            claims={
                "sub": self.subject,
                "email": self.email,
                "email_verified": self.email_verified,
                "name": self.name,
                "picture": "https://example.com/avatar.png",
            },
        )
        return f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}"

    def token(self, form: dict[str, str]) -> tuple[int, dict]:
        self.token_requests.append(form)
        pending = self.codes.pop(form.get("code", ""), None)
        if pending is None:
            return 400, {"error": "invalid_grant"}
        challenge = _b64url(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
        if challenge != pending.code_challenge:
            return 400, {"error": "invalid_grant", "error_description": "PKCE verification failed"}
        if form.get("client_id") != pending.client_id or form.get("redirect_uri") != pending.redirect_uri:
            return 400, {"error": "invalid_client"}
        access_token = secrets.token_urlsafe(24)
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": self.client_id,
            "iat": now,
            "exp": now + 300,
            "nonce": pending.nonce,
            "at_hash": at_hash(access_token),
            **pending.claims,
            **self.tamper,
        }
        key = self._foreign_key if self.sign_with_foreign_key else self._key
        id_token = jwt.encode(claims, self._pem(key).decode(), algorithm="RS256", headers={"kid": KID})
        return 200, {"access_token": access_token, "id_token": id_token, "token_type": "Bearer", "expires_in": 300}

    # ── transports ────────────────────────────────────────────────────
    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/jwks"):
                return httpx.Response(200, json=self.jwks())
            if request.url.path.endswith("/token"):
                form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
                status, body = self.token(form)
                return httpx.Response(status, json=body)
            return httpx.Response(404)

        return httpx.MockTransport(handler)


def _serve(port: int, email: str, client_id: str) -> None:
    import uvicorn
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, RedirectResponse
    from starlette.routing import Route

    provider = FakeOIDCProvider(issuer=f"http://localhost:{port}", client_id=client_id, email=email)

    async def authorize(request: Request):
        return RedirectResponse(provider.authorize(dict(request.query_params)), status_code=302)

    async def token(request: Request):
        form = {k: str(v) for k, v in (await request.form()).items()}
        status, body = provider.token(form)
        return JSONResponse(body, status_code=status)

    async def jwks(_request: Request):
        return JSONResponse(provider.jwks())

    app = Starlette(
        routes=[Route("/authorize", authorize), Route("/token", token, methods=["POST"]), Route("/jwks", jwks)]
    )
    print(json.dumps({"fake_oidc": f"http://localhost:{port}", "email": email, "client_id": client_id}))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9400)
    parser.add_argument("--email", default="owner@example.com")
    parser.add_argument("--client-id", default="test-client-id")
    args = parser.parse_args()
    _serve(args.port, args.email, args.client_id)
