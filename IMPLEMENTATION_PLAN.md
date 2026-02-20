# InterviewPilot — Implementation Plan

## Context

InterviewPilot is an AI-powered real-time interview assistant that reduces cognitive load for SDE interviewers. The system operates across three phases: **pre-interview planning** (resume analysis, question generation), **real-time live assistance** (audio capture, transcription, code analysis, progressive hints), and **post-interview reporting** (comprehensive assessment with annotated transcripts).

The repository is a **greenfield project** — completely empty. This plan covers the full implementation across 11 modules over 8 weeks (~490 engineering hours), following the PRD v2 and project plan v2 specifications.

---

## 1. Monorepo Structure

```
interview_pilot/
├── pnpm-workspace.yaml
├── turbo.json
├── package.json                    # Root: pnpm + Turborepo
├── .github/
│   └── workflows/
│       ├── ci-frontend.yml
│       ├── ci-backend.yml
│       ├── ci-extension.yml
│       └── deploy-staging.yml
├── docker-compose.yml              # Dev environment
├── docker-compose.prod.yml
├── .env.example
├── .gitignore
├── .prettierrc
├── .eslintrc.js
│
├── packages/
│   ├── shared-types/               # @copilot/shared-types
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   ├── src/
│   │   │   ├── index.ts
│   │   │   ├── interview.ts        # InterviewPlan, InterviewSession
│   │   │   ├── transcript.ts       # TranscriptEvent, SpeakerRole
│   │   │   ├── analysis.ts         # AnalysisEvent, CodeAnalysis
│   │   │   ├── hints.ts            # HintEvent, HintResponseMetrics
│   │   │   ├── reports.ts          # ReportData, Assessment
│   │   │   ├── websocket.ts        # WebSocketMessage protocol
│   │   │   ├── resilience.ts       # OfflineStateSnapshot, CatchUpSummary
│   │   │   └── llm.ts              # LLMProviderConfig, TaskType
│   │   └── tests/
│   │
│   └── sdk/                        # @copilot/sdk (shared utilities)
│       ├── package.json
│       └── src/
│           ├── index.ts
│           ├── token-counter.ts
│           └── time-utils.ts
│
├── apps/
│   ├── frontend/                   # @copilot/frontend — React + TypeScript
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   ├── vite.config.ts
│   │   ├── index.html
│   │   ├── public/
│   │   └── src/
│   │       ├── main.tsx
│   │       ├── App.tsx
│   │       ├── api/                # API client layer
│   │       │   ├── client.ts       # Axios/fetch wrapper
│   │       │   ├── websocket.ts    # WS client with reconnect
│   │       │   ├── auth.ts
│   │       │   ├── plans.ts
│   │       │   ├── sessions.ts
│   │       │   └── reports.ts
│   │       ├── stores/             # Zustand stores
│   │       │   ├── auth-store.ts
│   │       │   ├── session-store.ts
│   │       │   ├── transcript-store.ts
│   │       │   ├── analysis-store.ts
│   │       │   ├── hint-store.ts
│   │       │   └── offline-store.ts
│   │       ├── components/
│   │       │   ├── common/         # Button, Modal, ProgressRing, etc.
│   │       │   ├── auth/           # Login, GoogleSSO
│   │       │   ├── planning/       # ResumeUpload, PlanConfig, PlanEditor
│   │       │   ├── dashboard/      # TranscriptFeed, CodePanel, HintPanel
│   │       │   ├── reports/        # ReportView, ScoreMatrix, RadarChart
│   │       │   └── layout/         # Sidebar, Header, SessionBar
│   │       ├── hooks/
│   │       │   ├── useWebSocket.ts
│   │       │   ├── useOfflineMode.ts
│   │       │   └── useTimer.ts
│   │       ├── pages/
│   │       │   ├── LoginPage.tsx
│   │       │   ├── DashboardPage.tsx
│   │       │   ├── PlanningPage.tsx
│   │       │   ├── LiveSessionPage.tsx
│   │       │   └── ReportPage.tsx
│   │       ├── utils/
│   │       └── styles/
│   │
│   ├── backend/                    # @copilot/backend — FastAPI (Python)
│   │   ├── package.json            # For Turborepo task orchestration only
│   │   ├── pyproject.toml          # Python deps (Poetry/uv)
│   │   ├── alembic.ini
│   │   ├── alembic/
│   │   │   └── versions/
│   │   ├── Dockerfile
│   │   └── src/
│   │       ├── main.py             # FastAPI app entry
│   │       ├── config.py           # Settings (pydantic-settings)
│   │       ├── dependencies.py     # FastAPI DI
│   │       │
│   │       ├── models/             # SQLAlchemy ORM models
│   │       │   ├── __init__.py
│   │       │   ├── base.py         # Base model with UUID PK, timestamps
│   │       │   ├── user.py
│   │       │   ├── session.py
│   │       │   ├── plan.py
│   │       │   ├── transcript.py
│   │       │   ├── analysis.py
│   │       │   ├── hint.py
│   │       │   ├── report.py
│   │       │   └── question.py
│   │       │
│   │       ├── schemas/            # Pydantic request/response schemas
│   │       │   ├── __init__.py
│   │       │   ├── auth.py
│   │       │   ├── session.py
│   │       │   ├── plan.py
│   │       │   ├── transcript.py
│   │       │   ├── hint.py
│   │       │   └── report.py
│   │       │
│   │       ├── repositories/       # Data access layer (Repository pattern)
│   │       │   ├── __init__.py
│   │       │   ├── base.py         # BaseRepository[T] generic
│   │       │   ├── user_repo.py
│   │       │   ├── session_repo.py
│   │       │   ├── plan_repo.py
│   │       │   ├── transcript_repo.py
│   │       │   ├── hint_repo.py
│   │       │   └── report_repo.py
│   │       │
│   │       ├── services/           # Business logic layer
│   │       │   ├── __init__.py
│   │       │   ├── auth_service.py
│   │       │   ├── resume_service.py
│   │       │   ├── plan_service.py
│   │       │   ├── calibration_service.py
│   │       │   ├── question_service.py
│   │       │   ├── transcription_service.py
│   │       │   ├── speaker_service.py
│   │       │   ├── memory_service.py        # 3-tier hierarchical memory
│   │       │   ├── analysis_service.py      # Real-time utterance analysis
│   │       │   ├── contradiction_service.py
│   │       │   ├── code_analysis_service.py
│   │       │   ├── hint_service.py          # Progressive hint system
│   │       │   ├── session_state_service.py # Snapshot persistence
│   │       │   ├── catchup_service.py       # Retroactive catch-up
│   │       │   ├── report_service.py
│   │       │   └── report_generator.py
│   │       │
│   │       ├── llm/                # LLM Provider Abstraction (Strategy pattern)
│   │       │   ├── __init__.py
│   │       │   ├── interface.py    # ILLMProvider ABC
│   │       │   ├── config.py       # LLMProviderConfig, TaskTypeMapping
│   │       │   ├── factory.py      # ProviderFactory (Factory pattern)
│   │       │   ├── registry.py     # Provider registry + hot-reload
│   │       │   ├── health.py       # HealthMonitor (Circuit Breaker)
│   │       │   ├── failover.py     # FailoverManager
│   │       │   ├── providers/
│   │       │   │   ├── __init__.py
│   │       │   │   ├── anthropic.py    # Claude implementation
│   │       │   │   ├── openai.py       # GPT implementation
│   │       │   │   └── mock.py         # Mock for testing
│   │       │   └── decorators.py   # Logging, metrics, retry (Decorator pattern)
│   │       │
│   │       ├── api/                # API routes
│   │       │   ├── __init__.py
│   │       │   ├── router.py       # Main router aggregation
│   │       │   ├── auth.py         # /api/auth/*
│   │       │   ├── upload.py       # /api/upload/*
│   │       │   ├── plans.py        # /api/plans/*
│   │       │   ├── sessions.py     # /api/sessions/*
│   │       │   ├── questions.py    # /api/questions/* (admin)
│   │       │   └── reports.py      # /api/reports/*
│   │       │
│   │       ├── websocket/          # WebSocket handlers
│   │       │   ├── __init__.py
│   │       │   ├── manager.py      # ConnectionManager
│   │       │   ├── audio_handler.py
│   │       │   ├── code_handler.py
│   │       │   └── dashboard_handler.py
│   │       │
│   │       ├── workers/            # Background task processors
│   │       │   ├── __init__.py
│   │       │   ├── deepgram_worker.py
│   │       │   ├── analysis_worker.py
│   │       │   ├── snapshot_worker.py
│   │       │   └── replay_worker.py
│   │       │
│   │       └── utils/
│   │           ├── __init__.py
│   │           ├── token_budget.py
│   │           ├── text_extraction.py  # PDF/DOCX parsing
│   │           └── encryption.py
│   │
│   └── extension/                  # @copilot/extension — Chrome Extension
│       ├── package.json
│       ├── manifest.json           # Manifest V3
│       ├── tsconfig.json
│       ├── vite.config.ts          # Build config (CRXJS or similar)
│       ├── src/
│       │   ├── background/
│       │   │   └── service-worker.ts
│       │   ├── content/
│       │   │   ├── editor-detector.ts   # CodeMirror/Monaco/Ace/textarea
│       │   │   └── code-scraper.ts
│       │   ├── popup/
│       │   │   ├── Popup.tsx
│       │   │   └── popup.css
│       │   ├── audio/
│       │   │   ├── tab-capture.ts
│       │   │   ├── mic-capture.ts
│       │   │   └── audio-processor.ts
│       │   ├── networking/
│       │   │   ├── ws-client.ts         # WebSocket with reconnect
│       │   │   └── chunk-queue.ts
│       │   ├── storage/
│       │   │   └── offline-buffer.ts    # IndexedDB ring buffer
│       │   └── utils/
│       │       └── language-detect.ts
│       └── tests/
│
├── infra/                          # Infrastructure configs
│   ├── docker/
│   │   ├── backend.Dockerfile
│   │   ├── frontend.Dockerfile
│   │   └── mock-deepgram/
│   │       └── Dockerfile
│   ├── k8s/                        # Kubernetes manifests
│   │   ├── backend-deployment.yml
│   │   ├── frontend-deployment.yml
│   │   └── ingress.yml
│   └── grafana/
│       └── dashboards/
│           └── interview-pilot.json
│
├── docs/
│   └── adr/                        # Architecture Decision Records
│       ├── 001-monorepo-turborepo.md
│       ├── 002-llm-provider-abstraction.md
│       └── 003-session-resilience.md
│
└── scripts/
    ├── seed-questions.py           # Question bank seeder
    └── benchmark-latency.py        # STT latency benchmark
```

---

## 2. Design Patterns & Architectural Decisions

### 2.1 Backend Architecture (Clean Architecture / Layered)

```
API Routes (thin) → Services (business logic) → Repositories (data access)
                         ↓
                    LLM Abstraction (Strategy + Factory + Circuit Breaker)
```

- **Repository Pattern**: All DB access through repository classes. Services never touch SQLAlchemy directly. This enables swapping storage backends and simplifies testing with mock repositories.
- **Service Layer**: All business logic lives in services. Routes are thin — validate input, call service, return response.
- **Dependency Injection**: FastAPI's `Depends()` for injecting repos, services, and LLM providers into route handlers.

### 2.2 LLM Provider Abstraction (Strategy + Factory + Circuit Breaker)

```python
# interface.py — Abstract base
class ILLMProvider(ABC):
    async def complete(prompt: str, config: LLMConfig) -> AsyncIterator[str]  # streaming
    async def analyze(context: AnalysisContext, task: TaskType) -> StructuredOutput
    async def health_check() -> ProviderStatus

# factory.py — Creates providers from config
class ProviderFactory:
    def create(provider_type: str, config: dict) -> ILLMProvider

# health.py — Circuit Breaker pattern
class HealthMonitor:
    # Pings providers every 10s, tracks failure count
    # Opens circuit after 3 consecutive failures
    # Half-open after 30s to test recovery

# failover.py — Orchestrates task → provider routing
class FailoverManager:
    # Maps TaskType → (primary_provider, fallback_provider)
    # Routes calls based on health status
    # Logs all failover events
```

**Key decision**: Prompts are provider-agnostic — no Claude XML tags or OpenAI function-call format in business logic. The provider implementation adapts the generic prompt to its native format.

### 2.3 Real-Time Pipeline (Event-Driven / Observer)

```
Extension → [WebSocket] → Audio Handler → Deepgram Worker → Redis Stream
                                                                    ↓
                                              TranscriptEvent Consumer
                                                    ↓
                                        ┌───────────┼───────────┐
                                   Memory Svc   Analysis Svc   Fact Store
                                        └───────────┼───────────┘
                                                    ↓
                                        Dashboard WebSocket Push
```

- **Redis Streams** as the event bus for transcript events — supports multiple consumers, replay, and persistence.
- **WebSocket ConnectionManager** handles per-session connections with heartbeat and reconnection tracking.

### 2.4 Session Resilience (CQRS-inspired)

- **Write path**: Session state snapshots written to PostgreSQL every 30s and on significant events.
- **Read path**: Active state served from Redis (fast), with PostgreSQL as recovery source.
- **Extension buffering**: IndexedDB ring buffer (10 min audio, 100 code snapshots) with chronological drain on reconnect.
- **Catch-up pipeline**: Buffered → batch transcribe → fact extraction → consolidated analysis → catch-up notification.

### 2.5 Frontend State Management (Zustand + Event-Driven)

Separate Zustand stores per domain (session, transcript, analysis, hints, offline) to avoid monolithic state. WebSocket events dispatch to the appropriate store. `sessionStorage` snapshot for fast recovery on reconnect.

### 2.6 Progressive Hint System

```
Question Activated → Pre-compute solution patterns + 3-level hints per approach
                              ↓
Stuck Detected (or manual trigger) → Show Level 1 hint
                              ↓ (3 min timeout or manual advance)
                         Show Level 2 hint
                              ↓
                         Show Level 3 hint

Each hint → HintEvent record → persisted → feeds report scoring
```

---

## 3. Database Schema Design

### Core Tables (all use UUID PKs, created_at/updated_at timestamps)

```sql
-- Users & Auth
users (id, email, name, role, google_id, avatar_url)

-- Interview Planning
interview_sessions (id, user_id, status, level, duration_minutes,
                    focus_areas, job_description, started_at, ended_at)
interview_plans (id, session_id, plan_data JSONB, version, is_active)
question_bank (id, text, difficulty, category, tags[], expected_time_min,
               optimal_approaches JSONB, common_pitfalls JSONB,
               hints JSONB, alternates JSONB, is_retired)

-- Real-Time Data
transcripts (id, session_id, speaker_role, text, is_final, confidence,
             timestamp, word_timestamps JSONB, is_retroactive)
analysis_events (id, session_id, transcript_id, answer_quality, on_track,
                 insight, red_flags JSONB, suggestion, hint_signal)
code_snapshots (id, session_id, code, language, diff, approach,
                progress_state, timestamp)

-- Hint Tracking
hint_events (id, session_id, question_id, level, text, displayed_at,
             interviewer_used, candidate_response_quality, time_to_respond_ms)

-- Resilience
session_snapshots (id, session_id, snapshot_data JSONB, schema_version,
                   created_at)

-- Reports
reports (id, session_id, report_data JSONB, recommendation,
         confidence_level, is_final)
report_notes (id, report_id, user_id, note_text, created_at)

-- LLM Config
llm_provider_configs (id, task_type, primary_provider, primary_model,
                      fallback_provider, fallback_model, is_active)
llm_failover_log (id, session_id, task_type, from_provider, to_provider,
                  reason, timestamp)
```

### Indexes
- `transcripts`: (session_id, timestamp), (session_id, is_final)
- `hint_events`: (session_id, question_id), (session_id, displayed_at)
- `session_snapshots`: (session_id, created_at DESC)
- `analysis_events`: (session_id, created_at)

---

## 4. API Design

### REST Endpoints
```
POST   /api/auth/login              # Google SSO
POST   /api/auth/refresh            # JWT refresh
GET    /api/auth/me                 # Current user

POST   /api/upload/resume           # Upload PDF/DOCX → returns reference_id
GET    /api/upload/:id/status       # Extraction status

POST   /api/plans/create            # Create interview plan
GET    /api/plans/:id               # Get plan
PATCH  /api/plans/:id               # Edit plan (swap/reorder/adjust)
POST   /api/plans/:id/regenerate    # Regenerate specific question

POST   /api/sessions/start          # Start live session
PATCH  /api/sessions/:id/pause      # Pause/resume
POST   /api/sessions/:id/end        # End session → trigger report
GET    /api/sessions/:id            # Session details

POST   /api/sessions/:id/hints/next # Trigger next hint level
POST   /api/sessions/:id/hints/:hintId/ack  # Mark hint acknowledged

GET    /api/reports/:id             # Get report
POST   /api/reports/:id/notes       # Add note to report
GET    /api/reports/compare?ids=    # Compare reports

# Admin
GET    /api/questions               # List question bank
POST   /api/questions               # Add question
PATCH  /api/questions/:id           # Edit question
GET    /api/admin/llm-config        # View LLM provider config
PATCH  /api/admin/llm-config        # Update LLM provider config
```

### WebSocket Endpoints
```
ws://backend/ws/audio/:session_id     # Extension → Backend (audio chunks)
ws://backend/ws/code/:session_id      # Extension → Backend (code updates)
ws://backend/ws/dashboard/:session_id # Backend → Frontend (all events)
```

### WebSocket Message Protocol
```typescript
type WSMessage = {
  type: 'transcript' | 'analysis' | 'code_analysis' | 'hint' | 'red_flag'
      | 'phase_change' | 'catchup' | 'provider_status' | 'error';
  session_id: string;
  timestamp: string;
  payload: Record<string, unknown>;
}
```

---

## 5. Phased Implementation (Module by Module)

### Phase 1: Foundation (Week 1) — M0

**Step 1: Monorepo scaffolding** (M0-T1)
- Initialize pnpm workspace with `pnpm-workspace.yaml`
- Configure Turborepo (`turbo.json`) with build/test/lint pipelines
- Create all 5 package directories with their `package.json` files
- Python backend: use `pyproject.toml` with `uv` for dependency management
- Turborepo manages the frontend/extension/shared-types; backend has a shim `package.json` for Turborepo tasks that delegate to Python tooling

**Step 2: Shared types** (M0-T2)
- Define TypeScript interfaces in `packages/shared-types/src/`
- Mirror as Pydantic models in `apps/backend/src/schemas/`
- Use OpenAPI spec generation from FastAPI to keep them in sync
- Types to define: InterviewPlan, TranscriptEvent, CodeAnalysis, ReportData, WebSocketMessage, HintEvent, HintResponseMetrics, OfflineStateSnapshot, LLMProviderConfig

**Step 3: CI/CD** (M0-T3)
- GitHub Actions workflows: separate for frontend, backend, extension
- Pipeline: lint → type-check → unit-test → build → integration-test
- Deploy-to-staging on merge to main
- Use matrix builds for parallelism

**Step 4: Docker Compose dev environment** (M0-T4)
- Services: FastAPI (hot-reload), PostgreSQL 16, Redis 7, mock-deepgram, mock-llm
- Mock Deepgram: simple WebSocket server returning canned transcription
- Mock LLM: HTTP server returning canned responses per task type
- Frontend runs via Vite dev server (not containerized for faster HMR)

**Step 5: Database schema & migrations** (M0-T5)
- SQLAlchemy ORM models with `declarative_base`
- All models inherit from `BaseModel` with UUID pk, `created_at`, `updated_at`
- Alembic for migrations, auto-generate from model changes
- Create all tables as defined in Schema Design above

**Step 6: Auth** (M0-T6)
- Google OAuth2 via `authlib` or `python-social-auth`
- JWT access + refresh tokens via `python-jose`
- RBAC middleware: check role on protected routes
- Roles: `interviewer`, `admin`, `viewer`

**Step 7: Observability** (M0-T7)
- `structlog` for Python structured logging with trace_id propagation
- OpenTelemetry instrumentation for FastAPI, SQLAlchemy, Redis, HTTP clients
- Prometheus metrics endpoint (`/metrics`)
- Grafana dashboard JSON for key metrics

**Step 8: LLM Provider Abstraction** (M0-T8) [v2]
- `ILLMProvider` abstract base class with `complete()`, `analyze()`, `health_check()`
- `AnthropicProvider`: wraps `anthropic` SDK, adapts prompts to Claude format
- `OpenAIProvider`: wraps `openai` SDK, adapts prompts to GPT format
- `MockProvider`: deterministic responses for testing
- `ProviderFactory`: creates provider instances from config
- `HealthMonitor`: pings every 10s, circuit breaker (3 failures → open)
- `FailoverManager`: routes TaskType → provider, handles automatic switchover
- `@llm_logged` decorator: logs provider, model, tokens, latency, cost per call
- Config stored in `llm_provider_configs` table, hot-reloadable via Redis pub/sub

**Step 9: Session State Persistence** (M0-T9) [v2]
- `SessionStateService`: maintains in-memory state per session, writes snapshots
- Snapshot trigger: every 30s via background task + on significant events
- Snapshot format: versioned JSONB (schema_version field for backward compat)
- Recovery: on startup, load latest snapshot for any active sessions
- Target: snapshot write <50ms, recovery <5s

### Phase 2: Resume & Planning (Week 2) — M1, M2

**M1: Resume Parsing**
- `/api/upload/resume` endpoint: validate file type/size, store in MinIO
- `PyMuPDF` for PDF text extraction (handles multi-column, tables)
- `Tesseract` OCR fallback for scanned PDFs
- `python-docx` for DOCX extraction
- LLM extraction via `ILLMProvider(task='resume_extraction')`: structured JSON output with verifiable claims
- 10+ test resumes covering edge cases

**M2: Plan Generation**
- Level calibration engine: pure Python rules engine (dict mapping level → parameters)
- Question generation via `ILLMProvider(task='question_generation')` with web search capability
- Plan structuring: time-box phases, allocate per-question time, include buffers
- Plan edit API: swap, reorder, remove, add questions with auto-rebalancing
- Question bank: seed 50+ questions via `scripts/seed-questions.py`
- Each coding question gets 3 progressive hints pre-generated

### Phase 3: Chrome Extension (Week 3) — M3

- Manifest V3 with permissions: `tabCapture`, `activeTab`, `scripting`, `storage`
- Host permissions: `meet.google.com`, `zoom.us`, `teams.microsoft.com`
- **Tab audio**: `chrome.tabCapture.capture()` → `AudioContext` at 16kHz → 250ms PCM chunks
- **Mic audio**: `getUserMedia()` with echo cancellation → separate `AudioContext` → 250ms chunks
- **WebSocket client**: binary audio frames with channel metadata header, exponential backoff reconnect
- **Code scraper**: content script detecting CodeMirror 5/6 (`.CodeMirror`, `.cm-editor`), Monaco (`.monaco-editor`), Ace (`.ace_editor`), textarea fallback. `MutationObserver` + change listeners, 500ms debounce
- **Popup UI**: React, shows connection status, capture toggles, buffer level
- **Offline buffer** [v2]: IndexedDB ring buffer via `idb` library. 10 min audio (~9.6MB), 100 code snapshots. Chronological drain on reconnect with `buffered: true` flag and original timestamps

### Phase 4: STT & Analysis (Weeks 4-5) — M4, M5

**M4: STT Pipeline**
- Deepgram Nova-2 WebSocket connection: `model=nova-2, language=en, smart_format=true, interim_results=true, diarize=true, punctuate=true`
- Two parallel streams: tab (remote) + mic (interviewer)
- Speaker ID Layer 1: channel-based (mic=interviewer, tab=remote)
- Speaker ID Layer 2: Deepgram diarization within tab stream
- Speaker ID Layer 3: calibration prompt in dashboard first 60s
- `TranscriptEvent` objects pushed to Redis Stream + persisted to PostgreSQL
- **Buffered replay** [v2]: batch-mode transcription for `buffered: true` chunks, rate-limited, events marked `is_retroactive=true`

**M5: Memory & Analysis Engine**
- **Tier 1 Working Memory**: `collections.deque(maxlen=10)` of verbatim utterances, FIFO eviction to unsummarized buffer
- **Tier 2 Rolling Summary**: compress every 20 utterances via `ILLMProvider(task='summary_compression')`, <400 words, incorporates previous summary
- **Tier 3 Fact Store**: keyword-triggered LLM extraction → `claimed_experience`, `technical_positions`, `behavioral_signals`, `hint_interactions` dicts
- **Contradiction Detection**: rule-based negation pair first pass → LLM confirmation for ambiguous cases → contradiction event with suggested probe
- **Real-Time Analyzer**: on every final utterance, assemble context (system prompt + Tier 3 + Tier 2 + Tier 1 + current question + active hints) → `ILLMProvider(task='real_time_analysis')` streaming → first token <800ms
- **Token Budget Manager**: hard limits per tier (system ~800, T3 ~500, T2 ~1500, T1 ~2000, hints ~200, response ~300 = ~5300)
- **Catch-Up Processor** [v2]: on retroactive events → batch fact extraction → summary rebuild → single consolidated analysis → catch-up notification
- **Provider Failover** [v2]: wire to FailoverManager, log failovers, dashboard notification, provider-agnostic context format

### Phase 5: Code Analysis & Hints (Week 5) — M6

- **Code normalizer**: deduplicate multi-source, normalize whitespace, detect language, compute diff, 2s debounce
- **Solution pre-computation**: on question activation, `ILLMProvider(task='code_analysis')` generates optimal approaches, common bugs, edge cases, and 3-level hint paths per approach. Cached for question duration.
- **Approach detection**: match candidate code against pre-computed patterns
- **Progress tracking**: `empty → skeleton → partial → near_complete → complete`
- **Stuck detection**: code length oscillation over 5+ snapshots
- **Bug detection**: logic errors, off-by-one, missing edge cases via LLM analysis
- **Progressive Hints** [v2]:
  - Level 1 (Nudge): general directional, Socratic question
  - Level 2 (Guide): specific concept pointer
  - Level 3 (Direct): near-explicit, stops short of answer
  - Auto-advance after 3 min timeout (configurable)
  - HintEvent tracking: hint_id, level, text, displayed_at, interviewer_used, candidate_response_quality, time_to_respond_ms
- **Verbal-code correlation**: cross-reference transcript approach mentions with code approach detection

### Phase 6: Dashboard Frontend (Week 6) — M7

- **WebSocket client**: connects to `/ws/dashboard/:session_id`, handles all event types, auto-reconnect with sessionStorage state snapshot
- **Zustand stores**: separate stores for transcript, analysis, hints, offline state
- **Live transcript panel** (left): real-time utterances, speaker color-coding, inline AI insights (green/amber/red), auto-scroll with manual scroll-back, catch-up events with distinct visual style
- **Code analysis sidebar** (right): capture source, detected approach, progress bar, complexity, bug alerts, hint control panel
- **Hint control panel** [v2]: current hint level, "Show Hint" button, quotable hint text, "Candidate Got It" button, auto-advance countdown, hint timeline, keyboard shortcut (H)
- **Question tracker**: current question, countdown timer, plan progress, advance/swap controls
- **Score rings**: Problem Solving, Communication, Code Quality, Hint Responsiveness — animated transitions, trend indicators
- **Session controls bar**: LIVE/OFFLINE indicator, elapsed timer, Pause/Resume, End Interview (with confirmation), provider status
- **Manual code input**: toggleable CodeMirror panel, language auto-detect, manual override
- **Responsive**: 1280px (side-by-side), 1024px (compact), 768px (stacked). WCAG AA contrast. Keyboard nav. aria-live regions.
- **Offline mode** [v2]: 3 degradation levels
  - LLM down: transcript continues, cached hints available, manual scoring enabled
  - Backend down: extension buffers, local timer, manual notes textarea
  - Recovery: catch-up notification, notes sync, all features resume
  - Status bar: green → amber → red, no blocking error modals

### Phase 7: Shared Docs & Reports (Week 7) — M8, M9

**M8: Shared Document Integration** (P1 — deferrable)
- Google Docs Apps Script template with `onEdit` webhook
- Google Docs API polling fallback (every 2s, revision ID comparison)
- Notion API integration (poll every 2s, extract code blocks)
- Source selector in session setup UI

**M9: Report Generation**
- **Data aggregation**: on session end, collect transcript, analysis events, code events, fact store, contradictions, hint events, time-per-question, plan adherence, failover log → `ReportInput`
- **Report generation**: `ILLMProvider(task='report_generation', default=Opus)` with comprehensive prompt → recommendation (Strong Hire → No Hire), per-criterion scores with evidence, key moments, strengths, growth areas
- **Hint Responsiveness scoring**: derived from hint incorporation speed, quality (partial/full), independence trajectory, hint level ratio
- **Annotated transcript**: full transcript with inline annotations at correct timestamps
- **Key moments**: 5-10 significant moments (hint pickups, contradictions, bugs, standout answers)
- **Report UI**: executive summary, score matrix with progress bars, hint interaction panel, collapsible annotated transcript, PDF export (print stylesheet), shareable URL with auth
- **Comparison**: side-by-side radar chart (all scoring dimensions), candidate history

### Phase 8: Integration & Launch (Week 8) — M10

- **Full E2E test**: automated resume→plan→session→hints→report pipeline
- **Load test**: 10 concurrent sessions, verify <3s p95 latency, no message drops, stable memory
- **Security audit**: auth on all endpoints, no raw audio storage, AES-256 at rest, minimal extension permissions, keys in env vars
- **Resilience test suite** [v2]: 6 scenarios
  1. Kill primary LLM mid-session → verify failover
  2. Kill backend WebSocket → verify extension buffer + offline mode + catch-up
  3. Kill Deepgram → verify graceful pause + replay on recovery
  4. Kill Redis → verify PostgreSQL snapshot recovery
  5. 60-min interview with 3 forced failures → verify no data loss
  6. Kill all LLM providers → verify offline mode (timer, coverage, manual scoring, cached hints)
- **Bug fix sprint**: P0 → P1 → P2 prioritization
- **Production deployment**: Docker containers on K8s/ECS, managed PostgreSQL + Redis, CDN, TLS, alerting
- **Documentation**: auto-generated API docs (OpenAPI), ADRs, operational runbook, user guide

---

## 6. Testing Strategy

### Unit Tests (every module)
- Backend: `pytest` with `pytest-asyncio` for async code
- Frontend: `vitest` + `@testing-library/react`
- Extension: `vitest` for logic, manual test matrix for browser integration
- Mock LLM provider for all tests involving AI calls

### Integration Tests
- Resume → Plan pipeline (M2-T7): 3 levels, mocked LLM
- Code Analysis pipeline (M6-T7): simulated 20-min coding session
- STT pipeline: pre-recorded audio → transcript events

### E2E Tests
- Full flow: upload → plan → session → hints → report (M10-T1)
- Resilience scenarios (M10-T4)
- Load test: 10 concurrent sessions (M10-T2)

### Test Automation
- All unit/integration tests run in CI on every PR
- E2E tests run on merge to main before staging deploy
- Load tests run weekly or on-demand

---

## 7. Key Technical Decisions & Trade-offs

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Monorepo tool | Turborepo + pnpm | Fast, handles mixed JS/Python via shim package.json for backend |
| Python dependency manager | uv | Fast, modern, lockfile-based |
| Frontend framework | React + Vite | Fast HMR, mature ecosystem, Zustand for lightweight state |
| Backend framework | FastAPI | Async-native, WebSocket support, auto OpenAPI docs |
| LLM abstraction | Custom ILLMProvider | No existing library handles per-task routing + failover + health monitoring |
| Event bus | Redis Streams | Persistent, ordered, multi-consumer, lighter than Kafka for this scale |
| Session state | Redis (hot) + PostgreSQL (cold) | Speed for real-time, durability for recovery |
| Extension audio format | 16kHz 16-bit PCM | Deepgram native format, avoids re-encoding overhead |
| Offline buffer | IndexedDB | Only persistent storage available in Chrome extensions |
| Report LLM | Claude Opus (default) | Highest quality for deep analysis, not latency-sensitive |

---

## 8. Verification Plan

### Per-Module Verification
- **M0**: `pnpm install && pnpm build && pnpm test` from root succeeds. Docker Compose brings up all services. Auth flow works. LLM mock responds.
- **M1-M2**: Upload sample resume → get structured plan with hints. Test 3 different levels.
- **M3**: Extension loads, captures audio on Google Meet, scrapes CoderPad code, buffers during disconnect.
- **M4**: Pre-recorded audio → transcript events appear in Redis Stream with correct speaker labels.
- **M5**: Feed transcript events → verify memory tier updates, contradiction detection, analysis output.
- **M6**: Submit code snapshots → verify approach detection, stuck detection, hint generation.
- **M7**: Open dashboard → verify live transcript, code panel, hint controls, offline degradation.
- **M8**: Link Google Doc → verify code changes appear in pipeline.
- **M9**: End session → report generated with all sections including Hint Responsiveness.
- **M10**: Full E2E automated test passes. All 6 resilience scenarios pass. 10 concurrent sessions maintain <3s latency.

### End-to-End Smoke Test
1. Upload a sample resume PDF
2. Configure a 45-min SDE-2 interview with coding + system design focus
3. Start live session, simulate 15 min of audio + code
4. Trigger hints at a stuck point, verify 3-level progression
5. End session, verify report with Hint Responsiveness scoring
6. During session: simulate disconnect → verify buffer → reconnect → verify catch-up
