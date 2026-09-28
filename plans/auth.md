# Auth: Google login, owner/admin access, extension pairing

Decided: Google OAuth login · owner + admins can access a session · extension uses a per-session pairing token · auth ships before LangGraph Phase 1.

## Problem (reproduced against the real server with `ENVIRONMENT=production`)

- 26 REST routes had no auth. With no credentials, `GET /api/reports/{id}`, `/api/history/sessions/{id}/detail` and `/api/reports/` all returned `200` with candidate data.
- Nothing could issue a token: no login endpoint, no client sends credentials. So every route that *did* check auth (`/sessions/start`, `/history/sessions`, all three WebSockets) returned `401` in production.

## Credentials

| Credential | Issued by | Lifetime | Carried as | Accepted by |
|---|---|---|---|---|
| Access token (`type=access`) | `/api/auth/google/callback`, `/api/auth/refresh` | 30 min | `Authorization: Bearer` (REST); `?token=` (WS) | Every authenticated route, subject to owner/admin checks |
| Refresh token (`type=refresh`) | Same | 7 days | `HttpOnly; SameSite=Strict; Path=/api/auth` cookie (`Secure` outside dev) | `/api/auth/refresh` only |
| Pairing token (`type=pairing`, `sid` claim) | `POST /api/sessions/{id}/pairing-token` (owner/admin) | 6 h | `Authorization: Bearer` or `?token=` | Only routes and sockets for session `sid` |

The access token lives in memory in the frontend and is never persisted. On page load the app calls `/api/auth/refresh`, and the cookie restores the session.

## Login (OIDC authorization code + PKCE)

1. `GET /api/auth/google/start?next=/path` generates `state`, `nonce` and a PKCE verifier, stores them in Redis for 10 min under `state`, and returns the Google authorization URL.
2. Google redirects to the frontend's `/auth/callback?code&state`.
3. The frontend POSTs `{code, state}` to `/api/auth/google/callback`. The backend **pops** the state (single use), exchanges the code with the verifier, and verifies the ID token: signature against the provider's JWKS, `iss`, `aud`, `exp`, `nonce`, `email_verified`, and `at_hash`.
4. **Allowlist:** the verified email must be in `ALLOWED_LOGIN_EMAILS` or its domain in `ALLOWED_LOGIN_DOMAINS`. Otherwise `403`, and no user row is created. Production refuses to boot with both lists empty.
5. **Upsert:** look the user up by `google_id`, else link by verified email, else create. Refresh `name` and avatar. `role=admin` if the email is in `ADMIN_EMAILS`, otherwise `interviewer`, re-evaluated on every login.
6. Return the access token and `next`, and set the refresh cookie.

Provider endpoints are settings (`OIDC_*`) that default to Google, so tests and local end-to-end runs use a stand-in provider.

## Authorization

- `get_principal` resolves the bearer token to a `Principal(user_id, role, session_scope)`.
  - Access token: `session_scope=None`.
  - Pairing token: `session_scope=sid`.
  - In dev mode (`dev_mode` + `environment=development`) it still resolves to the seeded dev user.
- Resource dependencies return the resource or raise `404` (not `403`, so session IDs don't leak): `authorized_session`, `authorized_plan`, `authorized_report`, `authorized_upload`.
  - Access is allowed if the principal is admin, or the session's `user_id` equals the principal, and in either case `session_scope` is `None` or equals the session.
  - A malformed UUID returns `404`, where today it raises a `500`.
- **List endpoints** (`/reports/`, `/reports/compare`, `/history/sessions`) filter to the principal's sessions (admins see all) and **reject pairing tokens**.
- **WebSockets:**
  - `dashboard`: access token (owner/admin) or a pairing token for that session.
  - `audio` / `code`: same rule.
- **Resume uploads:** the Redis entry records the uploader, and `status` / `stream` check it.

## Extension pairing

- The live-session page has a **Connect extension** button that mints a pairing token.
  - If `VITE_EXTENSION_ID` is set, it sends `{type:'pair', token, backendUrl}` with `chrome.runtime.sendMessage(EXTENSION_ID, …)`. The manifest's `externally_connectable` lists the dashboard origins, and the service worker checks `sender.origin` against the stored frontend URL.
  - Otherwise it shows the token as a pairing code. The popup's **Session ID** field becomes **Pairing code**, and the session ID is read from the token's `sid` claim.
- The service worker keeps the token in `chrome.storage.session`, which survives service-worker eviction but not a browser restart. It passes the token to `WSClient.getAuthToken` for the audio and code sockets.
- The side panel iframe URL carries the token in the fragment (`#pairing=…`). The frontend in panel mode uses it as its bearer token for that session only.

## SSE

`EventSource` can't send headers, so resume-extraction progress switches to `fetch` with a bearer header and a streaming SSE parser. No token goes in the URL.

## Not in scope (follow-ups)

- **Refresh-token revocation and reuse detection:** tokens are stateless JWTs. Logout clears the cookie, but a stolen refresh token stays valid until it expires.
- **Real Google consent screen:** not verified locally, because no Google client credentials are configured. The flow is verified end to end against a stand-in OIDC provider.
