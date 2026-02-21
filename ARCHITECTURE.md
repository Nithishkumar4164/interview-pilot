# Interview Pilot — Architecture Document

> AI-Powered Real-Time Interview Assistant

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Monorepo Structure](#3-monorepo-structure)
4. [Backend Architecture](#4-backend-architecture)
5. [Frontend Architecture](#5-frontend-architecture)
6. [Chrome Extension Architecture](#6-chrome-extension-architecture)
7. [Shared Packages](#7-shared-packages)
8. [Data Flow & Communication](#8-data-flow--communication)
9. [Database Design](#9-database-design)
10. [LLM Provider Abstraction](#10-llm-provider-abstraction)
11. [Real-Time Processing Pipeline](#11-real-time-processing-pipeline)
12. [Key Design Patterns](#12-key-design-patterns)
13. [Resilience & Fault Tolerance](#13-resilience--fault-tolerance)
14. [Infrastructure & Deployment](#14-infrastructure--deployment)
15. [Security](#15-security)
16. [Testing Strategy](#16-testing-strategy)

---

## 1. System Overview

Interview Pilot is a full-stack platform that augments SDE interviewers with real-time AI capabilities. It captures audio and code from video-conferencing and coding platforms via a Chrome extension, transcribes speech in real time, performs multi-dimensional analysis of candidate responses, delivers progressive hints, and generates comprehensive post-interview reports.

### Core Capabilities

| Capability | Description |
|---|---|
| **Interview Planning** | Resume parsing via LLM, auto-generated question plans with phases, alternates, and pre-computed hints |
| **Real-Time Transcription** | Live speech-to-text via Deepgram Nova-2 with sub-second latency |
| **Speaker Identification** | 3-layer system: channel separation → diarization → voice calibration |
| **Utterance Analysis** | 6-dimension scoring: relevance, behavioral signals, consistency, hint reception, depth, communication |
| **Hierarchical Memory** | 3-tier context: working memory → rolling summary → fact store |
| **Progressive Hints** | 3 levels (Nudge → Guide → Direct) with time-based auto-advance |
| **Contradiction Detection** | Real-time inconsistency flagging across candidate statements |
| **Code Analysis** | code review with 5-dimensional rating and pitfalls |
| **Report Generation** | 7-criteria assessment with annotated transcript and hint usage |
| **Offline Resilience** | 3 degradation levels maintain functionality during connectivity loss |
| **LLM Failover** | Automatic provider switching between Anthropic Claude and OpenAI GPT |

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          INTERVIEW SESSION                              │
│                                                                         │
│  ┌──────────────────┐                         ┌──────────────────────┐  │
│  │  Chrome Extension │                         │   React Dashboard    │  │
│  │  (Manifest V3)    │                         │   (Vite + Zustand)   │  │
│  │                    │                         │                      │  │
│  │  ┌──────────────┐ │                         │  ┌────────────────┐  │  │
│  │  │ Tab Audio    │ │    /ws/audio/{id}       │  │ Transcript     │  │  │
│  │  │ Capture      │─┼───────────────────┐     │  │ Panel          │  │  │
│  │  └──────────────┘ │                   │     │  ├────────────────┤  │  │
│  │  ┌──────────────┐ │                   │     │  │ Analysis       │  │  │
│  │  │ Mic Audio    │─┼───────────────┐   │     │  │ Panel          │  │  │
│  │  │ Capture      │ │               │   │     │  ├────────────────┤  │  │
│  │  └──────────────┘ │               │   │     │  │ Hint Panel     │  │  │
│  │  ┌──────────────┐ │               │   │     │  ├────────────────┤  │  │
│  │  │ Code Scraper │─┼──────────┐    │   │     │  │ Plan Sidebar   │  │  │
│  │  │ (Content JS) │ │          │    │   │     │  └────────────────┘  │  │
│  │  └──────────────┘ │          │    │   │     │         ▲            │  │
│  └──────────────────┘          │    │   │     └─────────┼────────────┘  │
│                                 │    │   │               │               │
│  ┌──────────────────────────────┼────┼───┼───────────────┼─────────────┐│
│  │                    FASTAPI BACKEND (:8000)             │             ││
│  │                              │    │   │               │             ││
│  │  /ws/code/{id} ◄─────────────┘    │   │    /ws/dashboard/{id}       ││
│  │  /ws/audio/{id} ◄────────────────┘   │               │             ││
│  │         │                  ◄──────────┘               │             ││
│  │         ▼                                             │             ││
│  │  ┌──────────────┐   ┌─────────────┐   ┌──────────────┤             ││
│  │  │ Deepgram     │──►│ Analysis    │──►│ Dashboard WS │             ││
│  │  │ Worker       │   │ Pipeline    │   │ Broadcaster  │             ││
│  │  └──────────────┘   └──────┬──────┘   └──────────────┘             ││
│  │                            │                                        ││
│  │                     ┌──────▼──────┐                                 ││
│  │                     │  Services   │                                 ││
│  │                     │  Layer      │                                 ││
│  │                     └──────┬──────┘                                 ││
│  │                            │                                        ││
│  │            ┌───────────────┼───────────────┐                        ││
│  │            ▼               ▼               ▼                        ││
│  │     ┌────────────┐  ┌────────────┐  ┌────────────┐                 ││
│  │     │ PostgreSQL │  │   Redis    │  │   MinIO    │                 ││
│  │     │    16      │  │     7      │  │   (S3)     │                 ││
│  │     └────────────┘  └────────────┘  └────────────┘                 ││
│  └─────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
             ┌────────────┐  ┌────────────┐  ┌────────────┐
             │ Anthropic  │  │  OpenAI    │  │  Deepgram  │
             │ Claude API │  │  GPT API   │  │  Nova-2    │
             └────────────┘  └────────────┘  └────────────┘
```

---

## 3. Monorepo Structure

The project uses a **pnpm workspace + Turborepo** monorepo architecture.

```
interview-pilot/
├── apps/
│   ├── backend/                Python 3.12 — FastAPI server (:8000)
│   ├── frontend/               React 19 + TypeScript — Dashboard (:5173)
│   └── extension/              Chrome Manifest V3 — Audio/code capture
├── packages/
│   ├── shared-types/           Cross-service TypeScript type definitions
│   └── sdk/                    Utility library (token counter, time utils)
├── docker-compose.yml          Development infrastructure
├── docker-compose.prod.yml     Production deployment
├── turbo.json                  Turborepo task pipeline config
├── pnpm-workspace.yaml         Workspace package registry
└── .github/workflows/          CI/CD pipeline definitions
```

**Tooling:**

| Tool | Version | Purpose |
|---|---|---|
| pnpm | 9.15.0 | Package management + workspaces |
| Turborepo | latest | Task orchestration + caching across packages |
| Node.js | >= 20 | Runtime for frontend, extension, shared packages |
| Python | 3.12+ | Backend runtime |
| uv | latest | Python dependency management (replaces pip) |

---

## 4. Backend Architecture

### 4.1 Layered Architecture

The backend follows a strict **4-layer architecture** to separate concerns:

```
┌───────────────────────────────────────────────┐
│              API Layer (Routes)                │
│  REST handlers  │  WebSocket handlers          │
├───────────────────────────────────────────────┤
│            Service Layer (Business Logic)      │
│  Analysis │ Memory │ Hints │ Plans │ Reports   │
├───────────────────────────────────────────────┤
│          Repository Layer (Data Access)        │
│  Session │ Plan │ Transcript │ Report repos    │
├───────────────────────────────────────────────┤
│             Model Layer (ORM / Schema)         │
│  SQLAlchemy models  │  Pydantic schemas        │
└───────────────────────────────────────────────┘
```

### 4.2 API Layer (`src/api/`)

Seven route groups serve the REST API:

| Route Group | Prefix | Responsibilities |
|---|---|---|
| `auth` | `/api/auth` | JWT refresh, user profile, Google OAuth |
| `sessions` | `/api/sessions` | Session lifecycle, pause/resume, hint progression |
| `plans` | `/api/plans` | Plan CRUD, question swap/reorder, hint regeneration |
| `reports` | `/api/reports` | Report generation, listing, comparison, notes |
| `upload` | `/api/upload` | Resume upload, extraction status, SSE streaming |
| `history` | `/api/history` | Session history, filtering, pagination |
| `status` | `/api/status` | Health checks, provider health, API version |

### 4.3 WebSocket Layer (`src/websocket/`)

Three dedicated channels handle real-time communication:

| Channel | Direction | Wire Format | Purpose |
|---|---|---|---|
| `/ws/audio/{session_id}` | Extension → Backend | Binary (12-byte header + PCM) | Audio stream for transcription |
| `/ws/code/{session_id}` | Extension → Backend | JSON | Code snapshots and diffs |
| `/ws/dashboard/{session_id}` | Backend → Frontend | JSON | Event broadcasting |

**Binary audio frame format:**
```
Byte  0:       uint8    channel (0=tab, 1=mic)
Bytes 1-8:     float64  timestamp (big-endian, epoch ms)
Bytes 9-10:    uint16   sampleRate
Byte  11:      uint8    flags (bit0=buffered)
Bytes 12+:     bytes    PCM audio data (16-bit linear)
```

### 4.4 Service Layer (`src/services/`)

The service layer encapsulates all business logic. Each service is independently testable and receives dependencies via constructor injection.

#### Analysis Pipeline

| Service | Responsibility |
|---|---|
| `analysis_service` | 6-dimension utterance evaluation |
| `code_analysis_service` | Real-time code review, approach matching, progress tracking |
| `speaker_service` | 3-layer speaker identification |
| `transcription_service` | Deepgram WebSocket orchestration |
| `calibration_service` | Interview difficulty and time calibration |
| `contradiction_service` | Cross-utterance inconsistency detection |

#### Memory & Context

| Service | Responsibility |
|---|---|
| `memory_service` | 3-tier hierarchical context management |

The memory system maintains three tiers with token budgets to prevent context window overflow:

```
┌─────────────────────────────────┐
│  Working Memory (Tier 1)        │  Last 10 utterances, full verbatim text
├─────────────────────────────────┤
│  Rolling Summary (Tier 2)       │  Compressed history, < 400 words
├─────────────────────────────────┤
│  Fact Store (Tier 3)            │  Structured extraction: skills, experience,
│                                 │  technical positions, claimed projects
└─────────────────────────────────┘
```

#### Plan & Hints

| Service | Responsibility |
|---|---|
| `plan_service` | Question generation, time allocation, phase structuring |
| `hint_service` | 3-level progressive hints with timeout/auto-advance |
| `runtime_hint_service` | On-demand hint generation from real-time context |

**Progressive hint levels:**

```
Level 1 (Nudge)   →  General directional guidance
         ↓ timeout
Level 2 (Guide)   →  Specific concept pointer
         ↓ timeout
Level 3 (Direct)  →  Near-explicit answer, stops short of giving it away
```

#### Reporting

| Service | Responsibility |
|---|---|
| `report_generator` | 7-criterion post-interview assessment |

Criteria: problem solving, code quality, communication, hint responsiveness, system design, resume verification, behavioral signals.

#### Infrastructure Services

| Service | Responsibility |
|---|---|
| `auth_service` | JWT token creation and validation |
| `resume_service` | PDF/DOCX text extraction |
| `storage_service` | MinIO/S3 object storage |
| `persistence_service` | Async queue-based batch writer (2s flush / 50-item batches) |
| `session_state_service` | Periodic state snapshots for page recovery |

### 4.5 Repository Layer (`src/repositories/`)

Repositories abstract database access and provide typed query methods:

- `session_repo` — Session queries and lifecycle updates
- `plan_repo` — Plan CRUD operations
- `transcript_repo` — Transcript persistence and retrieval
- `analysis_repo` — Analysis event storage and queries
- `hint_repo` — Hint event tracking
- `report_repo` — Report queries
- `code_snapshot_repo` — Code submission history
- `question_repo` — Curated question bank access

### 4.6 Model Layer (`src/models/`)

SQLAlchemy 2.0+ models with async support:

| Model | Purpose |
|---|---|
| `User` | Auth user profile |
| `InterviewSession` | Session metadata, status, timing |
| `InterviewPlan` | Versioned question plans (JSON column) |
| `Transcript` | Speech events with timing and speaker info |
| `AnalysisEvent` | 6-dimension analysis results |
| `HintEvent` | Hint display and acknowledgment tracking |
| `CodeSnapshot` | Code submissions with diffs and progress |
| `Report` | Final assessment with scores and recommendations |
| `QuestionBank` | Curated questions with hints and alternates |

All models use UUID primary keys, JSON columns for flexible data, and cascade deletes for referential integrity.

### 4.7 Workers (`src/workers/`)

Background workers handle long-running or external-facing operations:

| Worker | Responsibility |
|---|---|
| `deepgram_worker` | Manages WebSocket to Deepgram Nova-2 (dual-channel, diarization, VAD) |
| `mock_deepgram_worker` | Simulator for development and testing |
| `analysis_worker` | Queues and processes real-time analysis requests |

---

## 5. Frontend Architecture

### 5.1 Technology Stack

| Technology | Version | Role |
|---|---|---|
| React | 19 | UI framework |
| TypeScript | 5.7 | Type safety (strict mode) |
| Vite | 6 | Build tooling and dev server |
| Zustand | 5 | Lightweight state management |
| React Router | 7 | Client-side routing |
| Vitest | latest | Unit testing |
| Testing Library | latest | Component testing |

### 5.2 Route Map

```
/                              SessionsPage         Interview history + new session entry
/new                           DashboardPage         Resume upload + plan setup
/session/:sessionId/plan       PlanReviewPage         Question review before go-live
/session/:sessionId            LiveSessionPage        Real-time interview dashboard
/reports                       ReportHistoryPage      Report archive + search
/reports/compare               ReportComparisonPage   Side-by-side candidate comparison
/reports/:reportId             ReportPage             Full post-interview assessment
```

### 5.3 State Management

Zustand stores are organized by feature domain. Each store is self-contained with its own actions and selectors.

```
┌─────────────────────────────────────────────────────────┐
│                    Zustand Stores                        │
│                                                          │
│  ┌───────────────┐  ┌───────────────┐  ┌──────────────┐ │
│  │ session-store │  │transcript-store│  │analysis-store│ │
│  │ Session meta, │  │ Live transcript│  │ 6-dim scores,│ │
│  │ status, timing│  │ feed, speakers │  │ red flags,   │ │
│  │               │  │               │  │ code analysis │ │
│  └───────────────┘  └───────────────┘  └──────────────┘ │
│  ┌───────────────┐  ┌───────────────┐  ┌──────────────┐ │
│  │  hint-store   │  │  plan-store   │  │speaker-store │ │
│  │ Hint history, │  │ Current plan, │  │ Identities,  │ │
│  │ response qual.│  │ question order│  │ color assign. │ │
│  └───────────────┘  └───────────────┘  └──────────────┘ │
│  ┌───────────────┐                                       │
│  │ offline-store │                                       │
│  │ WS status,    │                                       │
│  │ degradation   │                                       │
│  └───────────────┘                                       │
└─────────────────────────────────────────────────────────┘
```

### 5.4 Custom Hooks

| Hook | Purpose |
|---|---|
| `useWebSocket` | WebSocket connection manager with auto-reconnect (exponential backoff) |
| `useTimer` | Session countdown / elapsed time tracking |
| `useOfflineMode` | Degradation level detection and fallback logic |

### 5.5 Key Pages

**DashboardPage** — Interview setup: resume upload (PDF/DOCX), candidate details, level selection (intern → principal), focus area configuration, LLM-powered plan generation.

**PlanReviewPage** — Pre-interview planning: question tree view with timing, swap alternates, reorder, regenerate hints, transition to LIVE status.

**LiveSessionPage** — Real-time interview dashboard: session timer, transcript panel with speaker coloring, 6-dimension analysis rings, hint panel with progressive levels, plan sidebar, code overlay, speaker management.

**ReportPage** — Post-interview assessment: overall recommendation, 7-criterion scoring, annotated transcript, hint usage summary, key moments and quotes.

---

## 6. Chrome Extension Architecture

### 6.1 Manifest V3 Structure

```
extension/src/
├── manifest.json                  MV3 configuration + permissions
├── background/
│   └── service-worker.ts          Central orchestrator
├── offscreen/
│   └── offscreen.ts               Audio processing context (Web Audio API)
├── sidepanel/
│   ├── SidePanel.tsx              React UI — embeds dashboard in iframe
│   └── main.tsx
├── popup/
│   ├── Popup.tsx                  Connection status + session control
│   └── main.tsx
├── content/
│   └── code-scraper.ts            Content script for CoderPad, HackerRank, LeetCode
├── audio/
│   ├── tab-capture.ts             chrome.tabCapture API
│   ├── mic-capture.ts             User microphone capture
│   └── audio-processor.ts         AudioWorklet PCM encoder
├── networking/
│   ├── ws-client.ts               WebSocket connection manager
│   └── chunk-queue.ts             Buffering + retry logic
├── storage/
│   └── offline-buffer.ts          IndexedDB for offline persistence
└── utils/
    └── language-detect.ts         Programming language detection
```

### 6.2 Service Worker Orchestration

The service worker acts as the central coordinator:

```
┌────────────────────────────────────────────────┐
│              Service Worker                     │
│                                                 │
│  ┌─────────────┐    ┌─────────────────────────┐ │
│  │ Tab Capture  │───►│  Offscreen Document     │ │
│  │ (chrome.tab  │    │  (AudioWorklet +        │ │
│  │  Capture)    │    │   Web Audio API)        │ │
│  └─────────────┘    └──────────┬──────────────┘ │
│                                │                 │
│  ┌─────────────┐               │ PCM frames     │
│  │ Mic Capture  │──────────────┤                 │
│  └─────────────┘               ▼                 │
│                     ┌─────────────────────────┐  │
│                     │ WebSocket Client        │  │
│                     │ /ws/audio/{session_id}  │  │
│                     └─────────────────────────┘  │
│                                                  │
│  ┌─────────────┐    ┌─────────────────────────┐  │
│  │ Content      │───►│ WebSocket Client        │  │
│  │ Script       │    │ /ws/code/{session_id}   │  │
│  │ (code-       │    └─────────────────────────┘  │
│  │  scraper)    │                                 │
│  └─────────────┘    ┌─────────────────────────┐  │
│                     │ IndexedDB Offline Buffer │  │
│                     │ (drain on reconnect)     │  │
│                     └─────────────────────────┘  │
└────────────────────────────────────────────────┘
```

### 6.3 Supported Platforms

| Category | Platforms |
|---|---|
| Video Conferencing | Google Meet, Zoom, Microsoft Teams |
| Coding Platforms | CoderPad, HackerRank, LeetCode |

---

## 7. Shared Packages

### 7.1 `packages/shared-types/`

Cross-service TypeScript type definitions consumed by both frontend and extension:

- Session types: `InterviewSession`, `InterviewPlan`, `InterviewLevel`
- Transcript types: `TranscriptEvent`, `SpeakerRole`
- Analysis types: `AnalysisEvent` (6-dimension), `CodeAnalysis`
- Hint types: `HintEvent`, `HintLevel`, `HintResponseMetrics`
- Report types: `ReportData`, `Assessment`, `Recommendation`
- WebSocket types: `WSMessage`, `WSMessageType`
- Resilience types: `OfflineStateSnapshot`, `CatchUpSummary`
- LLM types: `LLMProviderConfig`, `LLMTaskType`, `ProviderStatus`

### 7.2 `packages/sdk/`

Shared utility library:

- `token-counter.ts` — Token usage estimation for context window budgeting
- `time-utils.ts` — Duration formatting, timestamp utilities

---

## 8. Data Flow & Communication

### 8.1 Interview Lifecycle

```
  ┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐
  │ PLANNING │─────►│  READY   │─────►│  LIVE    │─────►│  ENDED   │─────►│ REPORTED │
  └──────────┘      └──────────┘      └──────────┘      └──────────┘      └──────────┘
       │                 │                  │                  │                 │
  Resume upload    Plan review &       Audio/code         Session ends     Report generated
  Plan generation  question editing    streaming          Report triggered  available for view
  Hint pre-compute                     Real-time analysis
                                       Hint delivery
```

### 8.2 Real-Time Data Flow (LIVE Phase)

```
Extension                    Backend                       Frontend
────────                    ───────                       ────────
   │                           │                             │
   │  Binary audio frames      │                             │
   │──────────────────────────►│                             │
   │  /ws/audio/{id}           │                             │
   │                           │  Forward to Deepgram        │
   │                           │─────────────────►           │
   │                           │  Deepgram Nova-2            │
   │                           │◄─────────────────           │
   │                           │  Transcript events          │
   │                           │                             │
   │                           │  Analyze utterance          │
   │                           │  (6-dim + memory update)    │
   │                           │                             │
   │                           │  Broadcast via dashboard WS │
   │                           │────────────────────────────►│
   │                           │  /ws/dashboard/{id}         │
   │                           │                             │
   │  Code snapshots (JSON)    │                             │
   │──────────────────────────►│                             │
   │  /ws/code/{id}            │                             │
   │                           │  Analyze code               │
   │                           │  (approach, progress)       │
   │                           │                             │
   │                           │  Broadcast code analysis    │
   │                           │────────────────────────────►│
   │                           │                             │
```

### 8.3 REST API Communication

```
Frontend                      Backend                       Storage
────────                     ───────                       ───────
   │                            │                             │
   │  POST /api/upload/resume   │                             │
   │───────────────────────────►│  Extract text (LLM)         │
   │                            │  Store PDF in MinIO ────────►│
   │                            │                             │
   │  POST /api/plans/create    │                             │
   │───────────────────────────►│  Generate plan (LLM)        │
   │◄───────────────────────────│  Persist to PostgreSQL      │
   │                            │                             │
   │  POST /api/sessions/start  │                             │
   │───────────────────────────►│  Create session record      │
   │◄───────────────────────────│                             │
   │                            │                             │
   │  POST /api/reports/generate│                             │
   │───────────────────────────►│  Build report (LLM)         │
   │◄───────────────────────────│  7-criteria assessment      │
   │                            │                             │
```

---

## 9. Database Design

### 9.1 Entity Relationship Overview

```
┌──────────┐       ┌──────────────────┐       ┌────────────────┐
│   User   │──────►│ InterviewSession │◄──────│ InterviewPlan  │
└──────────┘  1:N  └────────┬─────────┘  1:1  └────────────────┘
                            │
              ┌─────────────┼─────────────┬────────────────┐
              │ 1:N         │ 1:N         │ 1:N            │ 1:N
              ▼             ▼             ▼                ▼
       ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌──────────────┐
       │ Transcript │ │AnalysisEvt │ │HintEvent │ │ CodeSnapshot │
       └────────────┘ └────────────┘ └──────────┘ └──────────────┘
                                                         │
                            ┌────────────────────────────┘
                            │ 1:1 (per session)
                            ▼
                      ┌──────────┐
                      │  Report  │
                      └──────────┘

       ┌──────────────┐
       │ QuestionBank │  (standalone, referenced by plans)
       └──────────────┘
```

### 9.2 Key Design Decisions

- **UUID primary keys** — Globally unique, safe for distributed generation
- **JSON columns** — Flexible storage for plan structures, analysis dimensions, and report data without schema rigidity
- **Async access** — All queries use `asyncpg` via SQLAlchemy's async engine, preventing I/O blocking on the event loop
- **Cascade deletes** — Deleting a session removes all child records (transcripts, analysis, hints, snapshots)
- **Alembic migrations** — Version-controlled schema evolution with 10+ migration files

### 9.3 Technology

| Component | Technology |
|---|---|
| RDBMS | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0+ (async) |
| Driver | asyncpg (async), psycopg2 (migrations) |
| Migrations | Alembic |

---

## 10. LLM Provider Abstraction

### 10.1 Interface-Based Design

The LLM subsystem uses an abstract interface to decouple business logic from provider implementations:

```
┌─────────────────────────────────────┐
│          ILLMProvider               │
│  (Abstract Interface)               │
│                                     │
│  + complete(prompt, config) → str   │
│  + analyze(context, schema) → dict  │
│  + health_check() → bool           │
└──────────┬──────────────────────────┘
           │
    ┌──────┼──────────────────┐
    │      │                  │
    ▼      ▼                  ▼
┌────────┐ ┌────────┐ ┌────────────┐
│Anthropic│ │ OpenAI │ │   Mock     │
│Provider │ │Provider│ │  Provider  │
│(Claude) │ │ (GPT)  │ │  (Tests)   │
└────────┘ └────────┘ └────────────┘
```

### 10.2 Task-Based Routing

Eight task types are routed to provider pairs (primary + fallback):

| Task Type | Primary | Fallback | Model (Primary) |
|---|---|---|---|
| Real-time analysis | Anthropic | OpenAI | Claude Sonnet 4 |
| Code analysis | Anthropic | OpenAI | Claude Sonnet 4 |
| Report generation | Anthropic | OpenAI | Claude Opus 4 |
| Hint generation | Anthropic | OpenAI | Claude Sonnet 4 |
| Summary compression | Anthropic | OpenAI | Claude Sonnet 4 |
| Resume extraction | Anthropic | OpenAI | Claude Sonnet 4 |
| Code rating | Anthropic | OpenAI | Claude Sonnet 4 |
| Question generation | Anthropic | OpenAI | Claude Sonnet 4 |

### 10.3 Health Monitoring & Failover

```
┌──────────────────────────────────────────────┐
│            Failover Manager                   │
│                                               │
│  Task Request                                 │
│       │                                       │
│       ▼                                       │
│  ┌─────────────────┐                          │
│  │ Health Monitor   │                          │
│  │                  │                          │
│  │ Per-provider:    │                          │
│  │  - Consecutive   │                          │
│  │    failures      │     ┌────────────────┐   │
│  │  - Avg latency   │────►│ Route to best  │   │
│  │  - Last success  │     │ available      │   │
│  └─────────────────┘     └────────┬───────┘   │
│                                    │           │
│              ┌─────────────────────┤           │
│              │ primary healthy?    │           │
│              ▼                     ▼           │
│         ┌────────┐          ┌────────┐        │
│         │Primary │          │Fallback│        │
│         └────────┘          └────────┘        │
└──────────────────────────────────────────────┘
```

The health monitor tracks consecutive failures and average latency per provider. When the primary exceeds the failure threshold, traffic is automatically routed to the fallback.

**Exposed via:** `GET /health/providers`

---

## 11. Real-Time Processing Pipeline

### 11.1 Audio → Analysis Pipeline

```
Audio Frame (binary)
       │
       ▼
┌──────────────────┐
│ Deepgram Worker   │
│                    │
│ - Dual-channel WS │
│ - Diarization     │
│ - VAD             │
│ - Smart formatting│
└────────┬─────────┘
         │ TranscriptEvent
         ▼
┌──────────────────┐     ┌──────────────────┐
│ Speaker Service   │────►│ Memory Service    │
│                    │     │                    │
│ Layer 1: Channel   │     │ Tier 1: Working   │
│ Layer 2: Diarize   │     │ Tier 2: Summary   │
│ Layer 3: Calibrate │     │ Tier 3: Facts     │
└────────┬─────────┘     └────────┬─────────┘
         │                        │
         ▼                        ▼
┌──────────────────────────────────────────┐
│           Analysis Service                │
│                                           │
│  6 Dimensions:                            │
│  1. Relevance                             │
│  2. Behavioral Signal                     │
│  3. Contextual Consistency                │
│  4. Hint Reception                        │
│  5. Depth & Completeness                  │
│  6. Communication Quality                 │
│                                           │
│  + Contradiction detection                │
│  + Red flag identification                │
└────────────────┬─────────────────────────┘
                 │
         ┌───────┴──────────┐
         ▼                  ▼
┌──────────────┐   ┌────────────────┐
│ Persistence   │   │ Dashboard WS   │
│ Service       │   │ Broadcaster    │
│ (batch write) │   │ (real-time)    │
└──────────────┘   └────────────────┘
```

### 11.2 Batch Persistence

The `PersistenceService` decouples the real-time WebSocket path from database writes:

- Events are buffered in an `asyncio.Queue`
- Flushed every **2 seconds** or when **50 items** accumulate (whichever comes first)
- Single database transaction per batch
- Non-blocking on the real-time event path

This ensures that database I/O latency never degrades the real-time experience.

---

## 12. Key Design Patterns

### 12.1 Service Layer Pattern

All business logic lives in `src/services/`, isolated from transport (HTTP/WS) and persistence concerns. Services are independently testable and reusable.

### 12.2 Repository Pattern

`src/repositories/` abstracts data access behind typed query interfaces, decoupling business logic from SQL and enabling mock-based testing.

### 12.3 Provider Abstraction (Strategy Pattern)

The `ILLMProvider` interface lets the system swap LLM backends transparently. Adding a new provider (e.g., Google Gemini) requires only implementing the interface — no changes to business logic.

### 12.4 Failover with Health Tracking

The failover manager implements the Circuit Breaker pattern: consecutive failures trip the circuit, routing traffic to the fallback provider until the primary recovers.

### 12.5 Hierarchical Memory

The 3-tier memory system (working → summary → facts) manages context across long interviews without exceeding token budgets, using compression at each tier boundary.

### 12.6 Async-First Architecture

The entire backend is built on `async/await`: FastAPI routes, SQLAlchemy queries (via asyncpg), WebSocket handlers, and background workers. No synchronous blocking on the event loop.

### 12.7 State Store Pattern (Frontend)

Zustand stores provide slice-based state management. Each feature domain (session, transcripts, analysis, hints) has its own store with actions and computed selectors, avoiding the boilerplate of Redux while maintaining predictability.

### 12.8 Batch Writer (Producer-Consumer)

The persistence service implements a producer-consumer pattern: real-time handlers produce events into an async queue, and a background consumer flushes them in batches, amortizing database overhead.

---

## 13. Resilience & Fault Tolerance

### 13.1 Three-Level Offline Degradation

```
Level 0 — FULLY CONNECTED
  All features operational
      │
      │ LLM provider failure
      ▼
Level 1 — LLM DEGRADED
  Transcription works, cached hints, manual scoring
      │
      │ Backend unreachable
      ▼
Level 2 — BACKEND DOWN
  Extension buffers audio/code to IndexedDB
  Frontend shows local timer, manual notes
      │
      │ All connectivity lost
      ▼
Level 3 — FULLY OFFLINE
  IndexedDB buffer, manual notes only
  Automatic drain on reconnect
```

### 13.2 Extension Offline Buffer

When the WebSocket connection drops, the extension stores audio chunks and code snapshots in **IndexedDB**. On reconnect, the `chunk-queue` drains the buffer in order, with retry logic for failed transmissions.

### 13.3 LLM Provider Failover

Automatic failover between Anthropic Claude (primary) and OpenAI GPT (fallback) based on health monitoring. Each task type has independent provider routing, so a failure in one provider doesn't affect tasks routed to another.

### 13.4 WebSocket Auto-Reconnect

The frontend `useWebSocket` hook implements exponential backoff reconnection. The dashboard WebSocket recovers session state via a catch-up mechanism that replays missed events.

### 13.5 Session State Snapshots

`SessionStateService` periodically snapshots the full session state to Redis. If the frontend reloads or reconnects, `GET /api/sessions/{id}/state` returns the latest snapshot for immediate recovery — no event replay needed.

---

## 14. Infrastructure & Deployment

### 14.1 Development Environment

```yaml
# docker-compose.yml
Services:
  postgres:    PostgreSQL 16 Alpine  (:5432)  — health checks enabled
  redis:       Redis 7 Alpine       (:6379)  — session cache
  minio:       MinIO                 (:9000)  — S3-compatible object store
  backend:     FastAPI               (:8000)  — uvicorn --reload
  frontend:    Vite                  (:5173)  — HMR dev server
```

### 14.2 Production Environment

```yaml
# docker-compose.prod.yml
Services:
  frontend:    Nginx Alpine          (:80)    — static asset serving
  backend:     Gunicorn + Uvicorn    (:8000)  — 2 workers, 120s timeout
  postgres:    PostgreSQL 16 Alpine           — persistent pgdata volume
  redis:       Redis 7 Alpine                 — internal network
  minio:       MinIO                          — persistent storage

Reverse Proxy: Caddy (HTTPS via DuckDNS)
```

### 14.3 Docker Build Strategy

**Backend (production):**
- Base: Python 3.12 slim
- Package manager: uv (deterministic installs)
- Non-root user (`appuser`)
- Gunicorn with UvicornWorker: 2 workers, 120s timeout (accommodates LLM call latency)
- `PYTHONPATH=/app` for Alembic import resolution

**Frontend (production):**
- Multi-stage build: Node 20 (build) → Nginx Alpine (serve)
- Turborepo-orchestrated workspace build
- Nginx serves static assets with SPA fallback

### 14.4 CI/CD Pipeline (GitHub Actions)

| Workflow | Triggers | Steps |
|---|---|---|
| `ci-backend.yml` | Push/PR to backend | Lint (Ruff) → Type-check (mypy) → Test (pytest) → Migration check |
| `ci-frontend.yml` | Push/PR to frontend | Lint (ESLint) → Type-check (tsc) → Test (Vitest) |
| `ci-extension.yml` | Push/PR to extension | Type-check (tsc) → Test (Vitest) |
| `deploy-staging.yml` | Manual/merge to main | Build containers → Deploy to Oracle Cloud VPS |

---

## 15. Security

### 15.1 Authentication

- **JWT-based** with access tokens (30 min TTL) and refresh tokens (7 day TTL)
- **HS256** signing with configurable secret key
- **Google OAuth 2.0** integration (optional)
- `HTTPBearer` dependency on all protected FastAPI routes

### 15.2 Data Protection

- Passwords hashed (not stored in plain text)
- Environment-based secrets (never committed)
- Non-root Docker containers in production
- Internal-only Redis (no auth exposed externally)
- MinIO credentials isolated to backend

### 15.3 Input Validation

- **Pydantic** schemas validate all API request bodies
- File upload size limits (10 MB for resumes)
- WebSocket message validation before processing

---

## 16. Testing Strategy

### 16.1 Backend (pytest)

| Test Type | Scope | Examples |
|---|---|---|
| Unit | Individual services | Memory hierarchy, analysis scoring, plan generation |
| Integration | API endpoints + DB | Session CRUD, report generation, LLM provider calls |
| E2E | Full pipeline | Transcript → analysis → hint → report lifecycle |

**Test infrastructure:**
- Mock LLM provider with controlled responses
- Async database fixtures with transaction rollback
- Factory-based test data generation

### 16.2 Frontend (Vitest + Testing Library)

| Test Type | Scope | Examples |
|---|---|---|
| Store tests | Zustand state logic | State transitions, action behavior |
| Component tests | UI rendering | Panel rendering, user interactions |

### 16.3 Type Safety

| Codebase | Tool | Configuration |
|---|---|---|
| Backend | mypy | Strict mode |
| Frontend | tsc | `--noEmit` strict mode |
| Extension | tsc | `--noEmit` strict mode |
| Shared types | tsc | Compiled + exported |

---

## Summary

Interview Pilot is a production-grade, real-time interview assistance platform built on a modern async stack. Its architecture prioritizes:

1. **Real-time performance** — Async-first backend, binary WebSocket protocol, batch persistence
2. **Resilience** — 3-level offline degradation, LLM failover, auto-reconnect, state snapshots
3. **Extensibility** — Interface-based LLM abstraction, modular service layer, shared type system
4. **Developer experience** — Monorepo with Turborepo, hot reload, comprehensive CI/CD, type safety across all codebases
5. **Separation of concerns** — 4-layer backend, feature-scoped frontend stores, dedicated extension workers
