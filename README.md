# Interview Pilot

**AI-Powered Real-Time Interview Assistant**

Reduces cognitive load and bias for SDE interviewers with real-time transcription, multi-dimensional analysis, progressive hints, and detailed report generation.

---

## Features

- **Interview Planning** — Upload a resume (PDF/DOCX), let the LLM parse it and generate a tailored question plan with phases, alternates, and pre-computed hints
- **Real-Time Transcription** — Live audio transcription via Deepgram Nova-2 with sub-second latency
- **3-Layer Speaker Identification** — Channel separation → diarization → calibration for accurate speaker attribution
- **6-Dimension Utterance Analysis** — Each candidate response scored on relevance, behavioral signals, consistency, hint reception, depth, and communication
- **3-Tier Hierarchical Memory** — Working memory → rolling summary → fact store keeps context across long interviews without losing signal
- **Progressive Hint System** — Three tiers (Nudge → Guide → Direct) plus runtime smart hints generated on demand
- **Contradiction Detection** — Flags inconsistencies between candidate statements in real time
- **Video Overlay Capture** — Chrome extension captures audio from Google Meet, Zoom, and Teams
- **Post-Interview Reports** — Automated report generation with recommendation, 7-criteria scoring, annotated transcript, and hint usage summary
- **Report Comparison** — Side-by-side comparison of candidates across sessions
- **Offline Resilience** — 3 degradation levels keep the session usable when connectivity drops
- **LLM Provider Failover** — Automatic failover between Anthropic Claude and OpenAI GPT
- **Chrome Extension** — Manifest V3 extension with side panel UI and audio capture

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
┌─────────────┐   REST API    ┌─────────────┐   SQL/async   ┌────────────┐
│   Frontend   │◄────────────►│   Backend    │◄────────────►│ PostgreSQL │
│   (React)    │              │  (FastAPI)   │              └────────────┘
└──────┬───────┘              └──┬───┬───┬───┘
       │                        │   │   │         ┌────────────┐
       │  /ws/dashboard/{id}    │   │   │         │   Redis    │
       └────────────────────────┘   │   │         └────────────┘
                                    │   │
┌─────────────┐  /ws/audio/{id}    │   │         ┌────────────┐
│  Extension   │◄──────────────────┘   │         │   MinIO    │
│  (Chrome)    │                                 │   (S3)     │
└─────────────┘                                  └────────────┘
```

Two dedicated WebSocket channels:
- **`/ws/audio/{session_id}`** — Streams binary audio frames from the extension to the transcription pipeline
- **`/ws/dashboard/{session_id}`** — Broadcasts real-time events (transcripts, analysis, hints, red flags) to the dashboard

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
cp .env.example .env
```

Fill in the required API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`) and any OAuth credentials.

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

On the meeting tab, press **Alt+Shift+I** or click **Start Capture** in the extension popup to start tab audio and the microphone together. Chrome only allows tab capture after you invoke the extension on that tab, so pairing alone can't start it. If the shortcut does nothing, check that Chrome assigned it at `chrome://extensions/shortcuts` (it stays unset when another extension already uses the key).

#### One-click pairing

By default the dashboard shows a pairing code that you paste into the extension popup. To have **Connect extension** hand the code to the extension directly, give the dashboard the extension's ID:

1. Copy the extension's ID from `chrome://extensions`
2. Add it to `apps/frontend/.env.local`:
   ```bash
   VITE_EXTENSION_ID=<extension-id>
   ```
3. Reload the dashboard (Vite restarts itself when an `.env` file changes)

An unpacked extension's ID is derived from the folder it was loaded from, so it changes on another machine or checkout. Set it again whenever you load the extension from a new path. The dashboard origin must also be listed under `externally_connectable` in `apps/extension/manifest.json` (`http://localhost:5173` and the production domain already are). If the ID is missing or wrong, the dashboard falls back to the pairing code.

The backend and dashboard URLs need no setup with one-click pairing. The dashboard sends its own origin as the backend URL (`ws(s)://<dashboard host>`), and its server forwards `/ws` to the backend (the Vite proxy in dev, `apps/frontend/nginx.conf` in production). The extension takes the dashboard URL from the sender origin Chrome reports. Both are saved in the extension's local storage.

With the pasted pairing code, the popup's URL fields default to `ws://localhost:8000` and `http://localhost:5173`, which is right for local dev. For production, enter `wss://interviewpilot.duckdns.org` and `https://interviewpilot.duckdns.org` once per Chrome profile; they are remembered after the first connect.

---

## Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `DATABASE_URL` | Async PostgreSQL connection string | Yes | `postgresql+asyncpg://copilot:copilot@localhost:5432/interview_pilot` |
| `DATABASE_URL_SYNC` | Sync PostgreSQL connection string (migrations) | Yes | `postgresql://copilot:copilot@localhost:5432/interview_pilot` |
| `REDIS_URL` | Redis connection string | Yes | `redis://localhost:6379/0` |
| `JWT_SECRET_KEY` | Secret for signing JWTs | Yes | `change-me-in-production` |
| `JWT_ALGORITHM` | JWT signing algorithm | No | `HS256` |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Access token TTL | No | `30` |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token TTL | No | `7` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | No | — |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | No | — |
| `GOOGLE_REDIRECT_URI` | OAuth redirect URI | No | `http://localhost:5173/auth/callback` |
| `ANTHROPIC_API_KEY` | Anthropic API key | Yes | — |
| `OPENAI_API_KEY` | OpenAI API key | Yes | — |
| `DEEPGRAM_API_KEY` | Deepgram API key | Yes | — |
| `S3_ENDPOINT` | MinIO / S3 endpoint | Yes | `http://localhost:9000` |
| `S3_ACCESS_KEY` | MinIO / S3 access key | Yes | `minioadmin` |
| `S3_SECRET_KEY` | MinIO / S3 secret key | Yes | `minioadmin` |
| `S3_BUCKET` | S3 bucket name | No | `interview-pilot` |
| `BACKEND_URL` | Backend base URL | No | `http://localhost:8000` |
| `FRONTEND_URL` | Frontend base URL | No | `http://localhost:5173` |
| `ENVIRONMENT` | Runtime environment | No | `development` |
| `LOG_LEVEL` | Logging verbosity | No | `DEBUG` |
| `VITE_EXTENSION_ID` | Chrome extension ID for one-click pairing (frontend, `apps/frontend/.env.local`; see [One-click pairing](#one-click-pairing)) | No | — |

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
│   │   │   ├── utils/            # Utility functions
│   │   │   └── App.tsx           # Root component with routing
│   │   └── package.json
│   └── extension/
│       ├── src/
│       │   ├── background/       # Service worker
│       │   ├── content/          # Content scripts
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
| `POST` | `/refresh` | Refresh JWT tokens |
| `GET` | `/me` | Get current user profile |

#### Sessions (`/api/sessions`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/start` | Create a new interview session |
| `GET` | `/{session_id}` | Get session metadata |
| `GET` | `/{session_id}/state` | Get full session state for page recovery |
| `POST` | `/{session_id}/go-live` | Transition from PLANNING to LIVE |
| `POST` | `/{session_id}/register-plan-hints` | Register pre-computed hints |
| `PATCH` | `/{session_id}/pause` | Toggle pause/resume |
| `POST` | `/{session_id}/end` | End session and trigger report |
| `DELETE` | `/{session_id}` | Delete session (PLANNING/READY only) |
| `POST` | `/{session_id}/hints/next` | Get next progressive hint |
| `POST` | `/{session_id}/hints/{hint_id}/ack` | Acknowledge hint delivery |

#### Plans (`/api/plans`)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/create` | Generate interview plan for a session |
| `GET` | `/{plan_id}` | Get plan by ID |
| `PATCH` | `/{plan_id}/swap` | Swap question with alternate |
| `PATCH` | `/{plan_id}/reorder` | Reorder questions in a phase |
| `PATCH` | `/{plan_id}/remove` | Remove a question |
| `PATCH` | `/{plan_id}/add` | Add a question |
| `POST` | `/{plan_id}/regenerate-hints` | Regenerate hints for a question |

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
| `/ws/dashboard/{session_id}` | Backend → Frontend | Real-time events: transcripts, analysis, hints, red flags, speaker updates |

---

## Chrome Extension

The Chrome extension captures audio from interview platforms and streams it to the backend via WebSocket.

**Capabilities:**
- Tab audio capture via offscreen document
- Side panel UI for session control
- IndexedDB offline buffer for resilience

**Supported Platforms:**

| Category | Platforms |
|----------|----------|
| Video conferencing | Google Meet, Zoom, Microsoft Teams |

**Installation:**
1. Run `pnpm dev` inside `apps/extension` to build with watch mode
2. Open `chrome://extensions` and enable **Developer mode**
3. Click **Load unpacked** → select `apps/extension/dist`
4. The Interview Pilot icon appears in your toolbar; open the side panel to connect to a session

---

## License

MIT
