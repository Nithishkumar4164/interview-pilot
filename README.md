# Interview Pilot

**AI-Powered Real-Time Interview Assistant**

Reduces cognitive load and bias for SDE interviewers with real-time transcription, multi-dimensional analysis, progressive hints, and detailed report generation.

---

## Features

- **Quick Session Start** — Start from an uploaded resume (PDF/DOCX, parsed by the LLM to prefill the candidate's details), from typed details, or as a guest; nothing is planned up front
- **In-Interview Question Generation** — Pick a category, take a suggested topic (or type one), and generate a question with hints, approaches, and pitfalls; regenerate with feedback, and only the questions you use are logged to the session's questionnaire
- **Real-Time Transcription** — Live audio transcription via Deepgram Nova-2 with sub-second latency
- **3-Layer Speaker Identification** — Channel separation → diarization → calibration for accurate speaker attribution
- **6-Dimension Utterance Analysis** — Each candidate response scored on relevance, behavioral signals, consistency, hint reception, depth, and communication
- **3-Tier Hierarchical Memory** — Working memory → rolling summary → fact store keeps context across long interviews without losing signal
- **Progressive Hint System** — Three tiers (Nudge → Guide → Direct) plus runtime smart hints generated on demand
- **Contradiction Detection** — Flags inconsistencies between candidate statements in real time
- **Peer Interviewer Copilot** — Suggested probes, claim checks, pacing and coverage, a running scorecard, and ask-the-copilot (behind `PEER_INTERVIEWER_ENABLED`)
- **Meeting Capture** — The extension captures the meeting tab's audio and your microphone, and relays the tab's video to the dashboard without a screen-share picker; the video is pinned above the transcript
- **Google Meet Controls** — Mute, camera, and present for the session's Meet tab from the dashboard's top bar; after capture starts, the extension switches back to the Interview Pilot tab
- **Code Capture** — Snapshots the candidate's code from CoderPad, HackerRank, and LeetCode
- **Configurable Layout** — Show or hide each live-session pane; the layout is remembered per browser
- **Post-Interview Reports** — Automated report generation with recommendation, 7-criteria scoring, annotated transcript, and hint usage summary
- **Report Comparison** — Side-by-side comparison of candidates across sessions
- **Offline Resilience** — 3 degradation levels keep the session usable when connectivity drops
- **LLM Provider Failover** — Automatic failover between Anthropic Claude and OpenAI GPT
- **Chrome Extension** — Manifest V3 extension, paired with a session in one click from the dashboard

---

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| **Frontend** | React 19, TypeScript, Vite 6, Zustand 5, React Router 7 |
| **Backend** | FastAPI, Python 3.12, SQLAlchemy (async), Alembic |
| **Extension** | Chrome Manifest V3, React 19, IndexedDB |
| **Database** | PostgreSQL 16, Redis 7 |
| **Storage** | MinIO (S3-compatible) |
| **AI** | Anthropic Claude, OpenAI GPT, Deepgram Nova-2 |
| **Monorepo** | pnpm workspaces, Turborepo |

---

## Architecture

```
interview-pilot/
├── apps/
│   ├── frontend/          React dashboard (Vite dev server :5173)
│   ├── backend/           FastAPI server (:8000)
│   └── extension/         Chrome extension (side panel + offscreen)
├── packages/
│   ├── shared-types/      TypeScript type definitions shared across apps
│   └── sdk/               Client SDK consumed by frontend & extension
├── docker-compose.yml     PostgreSQL, Redis, MinIO
├── turbo.json             Turborepo task pipeline
└── pnpm-workspace.yaml    Workspace configuration
```

**Communication:**

```
┌──────────────┐   REST /api      ┌──────────────┐  SQL (async)  ┌────────────┐
│  Dashboard   │◄────────────────►│   Backend    │◄─────────────►│ PostgreSQL │
│   (React)    │◄─────────────────│  (FastAPI)   │               └────────────┘
└──────▲───────┘  /ws/dashboard   └──────▲───────┘─────────────►  Redis, MinIO (S3)
       │                                 │
       │ meeting video (WebRTC),         │ /ws/audio
       │ status, Meet controls           │ /ws/code
┌──────┴───────┐                         │
│  Extension   │─────────────────────────┘
│  (Chrome)    │
└──────────────┘
```

Three WebSocket channels:
- **`/ws/audio/{session_id}`** — Streams binary audio frames from the extension to the transcription pipeline
- **`/ws/code/{session_id}`** — Streams code snapshots from the extension's content script
- **`/ws/dashboard/{session_id}`** — Broadcasts real-time events (transcripts, analysis, hints, red flags, copilot cards) to the dashboard

The dashboard and the extension also talk directly in the browser: the extension relays the meeting tab's video over a local WebRTC connection, reports its pairing and capture status, and carries Meet's mic, camera, and presenting state and controls.

---

## Prerequisites

- **Node.js** >= 20
- **Python** 3.12+
- **pnpm** (see `packageManager` field — v9.15.0)
- **uv** (Python package manager, used by the backend)
- **Docker** + **Docker Compose**

---

## Getting Started

### 1. Clone the repository

```bash
git clone <repo-url> interview-pilot
cd interview-pilot
```

### 2. Configure environment

```bash
cp apps/backend/.env.example apps/backend/.env
```

The backend reads `.env` from the directory it starts in (`apps/backend`), and every variable carries the `INTERVIEW_PILOT_` prefix. For a first run you can leave the keys empty:

- **LLM:** with no Anthropic or OpenAI key, a mock provider answers every call; one key is enough to use a real model.
  To run on your own Claude Code login instead, `uv sync --extra claude-code` and set `INTERVIEW_PILOT_DEV_LLM_PROVIDER=claude_code`. This mode is for personal local development on your own Claude account only: Anthropic's consumer terms don't allow a Claude subscription to power apps for other people, so never use it in a shared or deployed environment (the backend refuses to start with it outside `development`).
- **Transcription:** live transcripts need `INTERVIEW_PILOT_DEEPGRAM_API_KEY`.
- **Sign-in:** see [Signing in locally](#signing-in-locally).

### 3. Start infrastructure

```bash
docker-compose up -d
```

This launches PostgreSQL 16, Redis 7, and MinIO.

### 4. Install dependencies

```bash
pnpm install
```

### 5. Run the backend

```bash
cd apps/backend
pnpm migrate    # Apply database migrations
pnpm dev        # Start FastAPI on :8000
```

### 6. Run the frontend

```bash
cd apps/frontend
pnpm dev        # Start Vite dev server on :5173
```

Open the dashboard at http://interviewpilot.localhost:5173. Chrome and macOS resolve any `*.localhost` name to this machine, so no `/etc/hosts` entry is needed; `http://localhost:5173` also works, but Google sign-in returns to the `GOOGLE_REDIRECT_URI` host, and a sign-in is kept per address.

#### Signing in locally

The dashboard signs in with Google. Without a Google OAuth client, use the stand-in provider in `apps/backend/tests/fake_oidc.py`, which approves every sign-in as the email you give it:

```bash
cd apps/backend
PYTHONPATH=. uv run python -m tests.fake_oidc --port 9400 --email you@example.com
```

Then uncomment the "Local sign-in without Google" block in `apps/backend/.env` and restart the backend. With a real Google client instead, add `http://interviewpilot.localhost:5173/auth/callback` to its authorized redirect URIs.

### 7. Load the Chrome extension (optional)

```bash
cd apps/extension
pnpm dev        # Build with --watch
```

Then in Chrome:
1. Navigate to `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked** and select `apps/extension/dist`

#### Starting capture

Click **Connect extension** on the live session (with the session's Google Meet link) to pair the extension and open the Meet tab. On the meeting tab, press **Alt+Shift+I** (Option+Shift+I on Mac) or click **Start Capture** in the extension popup to start tab audio and the microphone together; the extension then switches back to the Interview Pilot tab, and the strip under the top bar shows that it is capturing. Chrome only allows tab capture after you invoke the extension on that tab, so pairing alone can't start it. If the shortcut does nothing, check that Chrome assigned it at `chrome://extensions/shortcuts` (it stays unset when another extension already uses the key).

#### One-click pairing

By default the dashboard shows a pairing code that you paste into the extension popup. To have **Connect extension** hand the code to the extension directly, give the dashboard the extension's ID:

1. Copy the extension's ID from `chrome://extensions`
2. Add it to `apps/frontend/.env.local`:
   ```bash
   VITE_EXTENSION_ID=<extension-id>
   ```
3. Reload the dashboard (Vite restarts itself when an `.env` file changes)

An unpacked extension's ID is derived from the folder it was loaded from, so it changes on another machine or checkout. Set it again whenever you load the extension from a new path. The dashboard origin must also be listed under `externally_connectable` in `apps/extension/manifest.json` (`http://interviewpilot.localhost:5173`, `http://localhost:5173` and the production domain already are). If the ID is missing or wrong, the dashboard falls back to the pairing code.

The backend and dashboard URLs need no setup with one-click pairing. The dashboard sends its own origin as the backend URL (`ws(s)://<dashboard host>`), and its server forwards `/ws` to the backend (the Vite proxy in dev, `apps/frontend/nginx.conf` in production). The extension takes the dashboard URL from the sender origin Chrome reports. Both are saved in the extension's local storage.

With the pasted pairing code, the popup's URL fields default to `ws://localhost:8000` and `http://interviewpilot.localhost:5173`, which is right for local dev. For production, enter `wss://interviewpilot.duckdns.org` and `https://interviewpilot.duckdns.org` once per Chrome profile; they are remembered after the first connect.

---

## Environment Variables

Backend variables live in `apps/backend/.env` and are read with the `INTERVIEW_PILOT_` prefix (for example `INTERVIEW_PILOT_DATABASE_URL`); `apps/backend/.env.example` lists them all. Outside development the backend refuses to start without a real JWT secret, an LLM key, a Deepgram key, a Google client, and a sign-in allowlist, and with `DEV_MODE` on.

| Variable (after `INTERVIEW_PILOT_`) | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | Async PostgreSQL connection string | `postgresql+asyncpg://copilot:copilot@localhost:5432/interview_pilot` |
| `DATABASE_URL_SYNC` | Sync PostgreSQL connection string (migrations) | `postgresql://copilot:copilot@localhost:5432/interview_pilot` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `JWT_SECRET_KEY` | Secret for signing JWTs (required outside development) | `change-me-in-production` |
| `JWT_ALGORITHM` | JWT signing algorithm | `HS256` |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Access token TTL | `30` |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token TTL | `7` |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth client; sign-in is off without it | — |
| `GOOGLE_REDIRECT_URI` | Where sign-in returns | `http://interviewpilot.localhost:5173/auth/callback` |
| `OIDC_ISSUER`, `OIDC_AUTHORIZATION_URL`, `OIDC_TOKEN_URL`, `OIDC_JWKS_URL` | OpenID provider endpoints; point them at the fake provider for local sign-in | Google's |
| `ALLOWED_LOGIN_EMAILS` / `ALLOWED_LOGIN_DOMAINS` | Who may sign in (comma-separated); empty allows anyone, development only | — |
| `ADMIN_EMAILS` | Users who get the admin role | — |
| `PAIRING_TOKEN_EXPIRE_HOURS` | Lifetime of the token the dashboard hands to the extension | `6` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | LLM providers; Anthropic is primary, OpenAI the fallback; with neither, a mock answers | — |
| `DEV_LLM_PROVIDER` | Without keys: `mock` or `scripted` (deterministic, input-sensitive). `claude_code` runs every task on your local Claude Code login, keys or not (development only; `uv sync --extra claude-code`) | `mock` |
| `DEEPGRAM_API_KEY` | Deepgram key for transcription | — |
| `PEER_INTERVIEWER_ENABLED` | Copilot acts as a peer interviewer | `false` |
| `AGENTIC_ROUTING_ENABLED` | Analysis chooses its own follow-ups | `false` |
| `REPORT_AGENTIC_SCORING` | Reports score each criterion with evidence tools | `false` |
| `S3_ENDPOINT` | MinIO / S3 endpoint | `http://localhost:9000` |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | MinIO / S3 credentials | `minioadmin` |
| `S3_BUCKET` | S3 bucket name | `interview-pilot` |
| `BACKEND_URL` | Backend base URL | `http://localhost:8000` |
| `FRONTEND_URL` | Dashboard origin (CORS) | `http://interviewpilot.localhost:5173` |
| `ENVIRONMENT` | `development` or `production` | `development` |
| `LOG_LEVEL` | Logging verbosity | `DEBUG` |
| `DEV_MODE` | API calls without a token act as a seeded dev user (development only) | `true` |

The dashboard has one variable of its own, `VITE_EXTENSION_ID` in `apps/frontend/.env.local`, for one-click pairing (see [One-click pairing](#one-click-pairing)).

---

## Project Structure

```
interview-pilot/
├── apps/
│   ├── backend/
│   │   ├── src/
│   │   │   ├── api/              # REST route handlers
│   │   │   ├── websocket/        # WebSocket handlers (audio, code, dashboard)
│   │   │   ├── services/         # Business logic (analysis, hints, reports, LLM)
│   │   │   ├── models/           # SQLAlchemy ORM models
│   │   │   ├── schemas/          # Pydantic request/response schemas
│   │   │   ├── llm/              # LLM provider clients and failover logic
│   │   │   ├── graphs/           # LangGraph session graph
│   │   │   ├── replay/           # Golden-transcript replay and scripted provider
│   │   │   ├── repositories/     # Data access layer
│   │   │   ├── workers/          # Background task workers
│   │   │   └── main.py           # FastAPI app entrypoint
│   │   ├── alembic/              # Database migrations
│   │   ├── tests/
│   │   └── package.json
│   ├── frontend/
│   │   ├── src/
│   │   │   ├── pages/            # Route pages (Dashboard, Session, Reports, etc.)
│   │   │   ├── components/       # Reusable UI components
│   │   │   ├── stores/           # Zustand state stores
│   │   │   ├── api/              # API client layer
│   │   │   ├── contexts/         # React contexts
│   │   │   ├── hooks/            # WebSocket, timer, and extension channel hooks
│   │   │   ├── lib/              # Helpers (meeting links, flags)
│   │   │   ├── styles/           # Global stylesheet
│   │   │   ├── __tests__/        # Vitest suites
│   │   │   └── App.tsx           # Root component with routing
│   │   └── package.json
│   └── extension/
│       ├── src/
│       │   ├── background/       # Service worker
│       │   ├── auth/             # Pairing tokens
│       │   ├── meeting/          # Google Meet tab: link, capture banner, controls
│       │   ├── content/          # Code capture content script
│       │   ├── sidepanel/        # Side panel React UI
│       │   ├── popup/            # Browser action popup UI
│       │   ├── offscreen/        # Offscreen document for audio processing
│       │   ├── audio/            # Audio capture and encoding
│       │   ├── mic-permission/   # Microphone permission flow
│       │   ├── networking/       # WebSocket client for backend communication
│       │   ├── storage/          # IndexedDB persistence layer
│       │   └── utils/
│       └── package.json
├── packages/
│   ├── shared-types/             # TypeScript types shared across apps
│   └── sdk/                      # Client SDK for API interaction
├── docker-compose.yml
├── turbo.json
├── pnpm-workspace.yaml
└── package.json
```

---

## Available Scripts

### Root (Turborepo)

| Script | Description |
|--------|-------------|
| `pnpm build` | Build all packages and apps |
| `pnpm dev` | Start all apps in development mode |
| `pnpm lint` | Lint all packages and apps |
| `pnpm test` | Run all test suites |
| `pnpm type-check` | Type-check all TypeScript packages |
| `pnpm clean` | Remove build artifacts |

### Backend (`apps/backend`)

| Script | Description |
|--------|-------------|
| `pnpm dev` | Start FastAPI with hot reload on `:8000` |
| `pnpm test` | Run pytest suite |
| `pnpm lint` | Lint with Ruff |
| `pnpm format` | Auto-format with Ruff |
| `pnpm migrate` | Apply Alembic migrations |
| `pnpm migrate:generate` | Generate a new migration |
| `pnpm type-check` | Run mypy |

### Frontend (`apps/frontend`)

| Script | Description |
|--------|-------------|
| `pnpm dev` | Start Vite dev server on `:5173` |
| `pnpm build` | Production build |
| `pnpm test` | Run Vitest suite |
| `pnpm lint` | Lint with ESLint |
| `pnpm preview` | Preview production build |
| `pnpm type-check` | Type-check with tsc |

### Extension (`apps/extension`)

| Script | Description |
|--------|-------------|
| `pnpm dev` | Build with `--watch` for development |
| `pnpm build` | Production build |
| `pnpm test` | Run Vitest suite |
| `pnpm lint` | Lint with ESLint |
| `pnpm type-check` | Type-check with tsc |

---

## API Overview

### REST Endpoints

#### Auth (`/api/auth`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/google/start` | Start Google sign-in (returns the authorization URL) |
| `POST` | `/google/callback` | Finish sign-in with the returned code |
| `POST` | `/refresh` | Refresh the access token (refresh cookie) |
| `POST` | `/logout` | Sign out |
| `GET` | `/me` | Get current user profile |

#### Sessions (`/api/sessions`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/start` | Create a new interview session |
| `GET` | `/{session_id}` | Get session metadata |
| `GET` | `/{session_id}/state` | Get full session state for page recovery |
| `POST` | `/{session_id}/go-live` | Start the interview (PLANNING to LIVE) |
| `POST` | `/{session_id}/register-plan-hints` | Register pre-computed hints |
| `PATCH` | `/{session_id}/meeting-url` | Set the session's Google Meet link |
| `POST` | `/{session_id}/pairing-token` | Token that pairs the extension with this session |
| `POST` | `/{session_id}/pause` | Pause the interview |
| `POST` | `/{session_id}/resume` | Resume the interview |
| `POST` | `/{session_id}/end` | End session and trigger report |
| `DELETE` | `/{session_id}` | Delete session (PLANNING/READY only) |
| `POST` | `/{session_id}/hints/next` | Get next progressive hint |
| `POST` | `/{session_id}/hints/{hint_id}/ack` | Acknowledge hint delivery |

#### Questions (`/api/sessions/{session_id}/questions`)
Questions are generated during the interview, not planned up front.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/topics` | Suggest topics for a category |
| `POST` | `/generate` | Generate a draft question (hints, approaches, pitfalls); pass rejected drafts and feedback to regenerate |
| `POST` | `` | Log the question the interviewer used to the questionnaire |
| `GET` | `` | The questionnaire, in order |

#### Reports (`/api/reports`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/generate/{session_id}` | Generate report for ended session |
| `GET` | `/` | List all reports (paginated) |
| `GET` | `/compare` | Compare reports side-by-side |
| `GET` | `/{report_id}` | Get full report |
| `GET` | `/{report_id}/status` | Check generation status |
| `POST` | `/{report_id}/notes` | Add note to report |

#### History (`/api/history`)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/sessions` | List sessions with filters and pagination |
| `GET` | `/sessions/{session_id}/detail` | Expanded session detail |
| `GET` | `/sessions/{session_id}/report` | Get session report |

#### Upload (`/api/upload`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/resume` | Upload resume (PDF/DOCX, max 10 MB) |
| `GET` | `/{reference_id}/status` | Check extraction status |
| `GET` | `/{reference_id}/stream` | SSE stream for extraction progress |

#### System
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/health/providers` | LLM provider health status |
| `GET` | `/api/status` | API operational status and version |

### WebSocket Endpoints

| Endpoint | Direction | Description |
|----------|-----------|-------------|
| `/ws/audio/{session_id}` | Extension → Backend | Binary audio frames with 12-byte header (channel, timestamp, sample rate) |
| `/ws/code/{session_id}` | Extension → Backend | Code snapshots from CoderPad, HackerRank, and LeetCode |
| `/ws/dashboard/{session_id}` | Backend → Frontend | Real-time events: transcripts, analysis, hints, red flags, speaker updates |

---

## Chrome Extension

The Chrome extension captures the interview and streams it to the backend.

**Capabilities:**
- Tab audio and microphone capture via an offscreen document, started with Alt+Shift+I (Option+Shift+I on Mac) on the meeting tab
- Meeting video relayed to the dashboard over a local WebRTC connection
- Google Meet: opens the session's Meet tab, shows a start-capture banner, pauses and resumes the session with the tab, and takes mute, camera, and present from the dashboard
- Code snapshots from CoderPad, HackerRank, and LeetCode
- Popup and side panel for pairing by code and session status
- IndexedDB offline buffer for resilience

**Supported Platforms:**

| Category | Platforms |
|----------|----------|
| Video conferencing | Google Meet (full support); any other meeting tab for audio and video capture |
| Coding | CoderPad, HackerRank, LeetCode |

**Installation:**
1. Run `pnpm dev` inside `apps/extension` to build with watch mode
2. Open `chrome://extensions` and enable **Developer mode**
3. Click **Load unpacked** → select `apps/extension/dist`
4. Set `VITE_EXTENSION_ID` for one-click pairing (see [One-click pairing](#one-click-pairing)), then click **Connect extension** on a live session

---

## License

MIT
