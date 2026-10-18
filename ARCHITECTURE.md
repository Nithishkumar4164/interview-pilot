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

Interview Pilot is a full-stack platform that augments SDE interviewers with real-time AI capabilities. It captures audio from video-conferencing platforms via a Chrome extension, transcribes speech in real time, performs multi-dimensional analysis of candidate responses, delivers progressive hints, and generates comprehensive post-interview reports.

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
│  │  └──────────────┘ │               │   │     │  ├────────────────┤  │  │
│  │                    │               │   │     │  │ Plan Sidebar   │  │  │
│  │                    │               │   │     │  └────────────────┘  │  │
│  └──────────────────┘               │   │     │         ▲            │  │
│                                      │   │     └─────────┼────────────┘  │
│                                      │   │               │               │
│  ┌───────────────────────────────────┼───┼───────────────┼─────────────┐│
│  │                    FASTAPI BACKEND (:8000)             │             ││
│  │                                   │   │               │             ││
│  │  /ws/audio/{id} ◄────────────────┘   │    /ws/dashboard/{id}       ││
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
│   └── extension/              Chrome Manifest V3 — Audio capture
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
| `questions` | `/api/sessions/{id}/questions` | Topic suggestions, question generation during the interview, the questionnaire |
| `reports` | `/api/reports` | Report generation, listing, comparison, notes |
| `upload` | `/api/upload` | Resume upload, extraction status, SSE streaming |
| `history` | `/api/history` | Session history, filtering, pagination |
| `status` | `/api/status` | Health checks, provider health, API version |

### 4.3 WebSocket Layer (`src/websocket/`)

Two dedicated channels handle real-time communication:

| Channel | Direction | Wire Format | Purpose |
|---|---|---|---|
| `/ws/audio/{session_id}` | Extension → Backend | Binary (12-byte header + PCM) | Audio stream for transcription |
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

The service layer holds the business logic as plain, independently testable functions (prompt assembly, LLM calls, parsing, heuristics). The real-time services keep no state of their own: session state lives in the LangGraph graphs (§4.8), whose nodes call these functions.

#### Analysis Pipeline

| Service | Responsibility |
|---|---|
| `analysis_service` | 6-dimension utterance evaluation; the follow-up actions an analysis may choose (agentic and peer) |
| `peer_interviewer` | Peer interviewer (`PEER_INTERVIEWER_ENABLED`): prompts and parsing for suggested probes, claim checks, clarifications, interviewer coaching and edge cases; the deterministic signals (talk share, monologue, pacing, stuck, coverage, difficulty); the scorecard notes; the one copilot-card shape |
| `copilot_query` | Ask the copilot: a tool loop over the live session's evidence (the agentic report's index, built from pipeline state) with validated transcript citations |
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
| `question_service` | Topic suggestions and one-at-a-time question generation (hints, approaches, pitfalls) |
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
| `report_generator` | 7-criterion post-interview assessment (single LLM call; the default) |
| `report_agentic` | Per-criterion scoring with evidence tools and validated transcript citations, then one synthesis (`REPORT_AGENTIC_SCORING`, off by default) |

Criteria: problem solving, code quality, communication, hint responsiveness, system design, resume verification, behavioral signals.

#### Infrastructure Services

| Service | Responsibility |
|---|---|
| `auth_service` | JWT token creation and validation |
| `resume_service` | PDF/DOCX text extraction |
| `storage_service` | MinIO/S3 object storage |
| `persistence_service` | Async queue-based batch writer (2s flush / 50-item batches), idempotent inserts |

### 4.5 Repository Layer (`src/repositories/`)

Repositories abstract database access and provide typed query methods:

- `session_repo` — Session queries and lifecycle updates
- `plan_repo` — Plan CRUD operations
- `transcript_repo` — Transcript persistence and retrieval
- `analysis_repo` — Analysis event storage and queries
- `hint_repo` — Hint event tracking
- `report_repo` — Report queries
- `question_repo` — Curated question bank access

### 4.6 Model Layer (`src/models/`)

SQLAlchemy 2.0+ models with async support:

| Model | Purpose |
|---|---|
| `User` | Auth user profile |
| `InterviewSession` | Session metadata, status, timing |
| `SessionQuestion` | The questionnaire: each question used in a session, in order, with when it was asked |
| `InterviewPlan` | Up-front question plans of older sessions (read-only) |
| `Transcript` | Speech events with timing and speaker info |
| `AnalysisEvent` | 6-dimension analysis results |
| `HintEvent` | Hint display and acknowledgment tracking |
| `Report` | Final assessment with scores and recommendations |
| `QuestionBank` | Curated questions with hints and alternates |

All models use UUID primary keys, JSON columns for flexible data, and cascade deletes for referential integrity.

### 4.7 Workers (`src/workers/`)

Background workers handle long-running or external-facing operations:

| Worker | Responsibility |
|---|---|
| `deepgram_worker` | Manages WebSocket to Deepgram Nova-2 (dual-channel, diarization, VAD) |
| `mock_deepgram_worker` | Simulator for development and testing |
| `session_pipeline` | The `SessionPipeline` protocol handlers use, and its factory |
| `langgraph_session` | `LangGraphSessionPipeline`: transport (dashboard pushes, transcript persistence), the per-session ingress queue, batching and the 3 s analysis rate limit, the effect sink, background summary compression and code-LLM refinement |

### 4.8 Graphs (`src/graphs/`)

LLM workflows are LangGraph graphs. Nodes read state, call the service functions through the `FailoverManager`, and return state updates; they never touch the dashboard or the DB directly. Side effects are streamed out (`get_stream_writer`) and performed by the owning pipeline, keyed so a resumed run cannot write a row twice.

| Graph | Thread | Purpose |
|---|---|---|
| `session_graph` | `{session_id}` | Live interview: memory tiers, utterance analysis, fact extraction (in parallel), contradiction check, hint signal; optional agentic follow-ups (`AGENTIC_ROUTING_ENABLED`); optional peer interviewer (`PEER_INTERVIEWER_ENABLED`, §11.1) |
| `code_graph` | `code:{session_id}` | Live code tracking on its own queue, so editor snapshots never wait behind an utterance analysis |
| `report_graph` | `report:{report_id}` | Post-interview report; resumes after a restart without repeating finished steps |

`checkpointer.py` owns the Postgres checkpointer (psycopg3 pool, plus a separate pool for per-run advisory locks) and `serde.py` the explicit allowlist of types that may be stored in checkpoints. Session threads are deleted when the session ends; report threads when the report is persisted.

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
│  ┌───────────────┐  ┌───────────────┐                    │
│  │ offline-store │  │ copilot-store │                    │
│  │ WS status,    │  │ Copilot cards,│                    │
│  │ degradation   │  │ coverage,     │                    │
│  │               │  │ scorecard, Q&A│                    │
│  └───────────────┘  └───────────────┘                    │
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


**LiveSessionPage** — Real-time interview dashboard: session timer, transcript panel with speaker coloring, 6-dimension analysis rings, hint panel with progressive levels, plan sidebar, speaker management.

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
│                     ┌─────────────────────────┐  │
│                     │ IndexedDB Offline Buffer │  │
│                     │ (drain on reconnect)     │  │
│                     └─────────────────────────┘  │
└────────────────────────────────────────────────┘
```

### 6.3 Supported Platforms

| Category | Platforms |
|---|---|
| Video Conferencing | Google Meet, Zoom, Microsoft Teams |

---

## 7. Shared Packages

### 7.1 `packages/shared-types/`

Cross-service TypeScript type definitions consumed by both frontend and extension:

- Session types: `InterviewSession`, `InterviewPlan`, `InterviewLevel`
- Transcript types: `TranscriptEvent`, `SpeakerRole`
- Analysis types: `AnalysisEvent` (6-dimension)
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
   │  POST /api/sessions/start  │                             │
   │───────────────────────────►│  Create session record      │
   │◄───────────────────────────│                             │
   │                            │                             │
   │  POST .../questions/generate                             │
   │───────────────────────────►│  Generate a question (LLM)  │
   │  POST .../questions        │  Log the used question ────►│
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
              ┌─────────────┼─────────────┐
              │ 1:N         │ 1:N         │ 1:N
              ▼             ▼             ▼
       ┌────────────┐ ┌────────────┐ ┌──────────┐
       │ Transcript │ │AnalysisEvt │ │HintEvent │
       └────────────┘ └────────────┘ └──────────┘

                      ┌──────────┐
                      │  Report  │  1:1 (per session)
                      └──────────┘

       ┌──────────────┐
       │ QuestionBank │  (standalone, referenced by plans)
       └──────────────┘
```

### 9.2 Key Design Decisions

- **UUID primary keys** — Globally unique, safe for distributed generation
- **JSON columns** — Flexible storage for plan structures, analysis dimensions, and report data without schema rigidity
- **Async access** — All queries use `asyncpg` via SQLAlchemy's async engine, preventing I/O blocking on the event loop
- **Cascade deletes** — Deleting a session removes all child records (transcripts, analysis, hints, code snapshots)
- **Checkpoint tables** — `checkpoints`, `checkpoint_writes`, `checkpoint_blobs` and `checkpoint_migrations` belong to the LangGraph checkpointer (created by its `setup()`, excluded from Alembic)
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
│  + analyze(context, config) → out   │
│  + invoke_with_tools(system,        │
│      messages, tools, config) → turn│
│  + health_check() → status          │
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

`invoke_with_tools` runs one model turn of a provider-neutral tool conversation; `src/llm/tools.py` runs loops over it (step cap, forced final answer). Anthropic tool turns use prompt caching for the repeated system prompt and tools. The Anthropic provider omits `temperature` for models that reject it and ignores thinking blocks in responses.

### 10.2 Task-Based Routing

Each task type is routed to a provider pair (primary + fallback), in `src/llm/config.py`:

| Task Type | Primary | Fallback | Model (Primary) |
|---|---|---|---|
| Real-time analysis | Anthropic | OpenAI | `claude-sonnet-5` |
| Code analysis / code rating | Anthropic | OpenAI | `claude-sonnet-5` |
| Report generation (and agentic synthesis) | Anthropic | OpenAI | `claude-opus-5-5` |
| Report criterion (agentic scoring) | Anthropic | OpenAI | `claude-sonnet-5` |
| Peer interviewer (probes, claim checks, clarifications, edge cases) | Anthropic | OpenAI | `claude-sonnet-5` |
| Interviewer coaching | Anthropic | OpenAI | `claude-haiku-5-5` |
| Copilot query (ask the copilot) | Anthropic | OpenAI | `claude-sonnet-5` |
| Hint generation | Anthropic | OpenAI | `claude-sonnet-5` |
| Summary compression | Anthropic | OpenAI | `claude-sonnet-5` |
| Resume extraction | Anthropic | OpenAI | `claude-sonnet-5` |
| Question generation | Anthropic | OpenAI | `claude-sonnet-5` |

`tests/test_live_models.py` (opt-in, `LIVE_LLM=1`) checks every configured model exists.

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

Every call emits an `llm.analyze` / `llm.invoke_with_tools` OpenTelemetry span with provider, model, tokens, latency and failover (metadata only, never prompt text); spans are exported only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

**Exposed via:** `GET /health/providers`

---

## 11. Real-Time Processing Pipeline

### 11.1 Audio → Analysis Pipeline

```
Audio Frame ──► Deepgram Worker ──► Speaker Service ──► TranscriptEvent
                                                            │
                                     LangGraphSessionPipeline (one per session)
                                                            │
        transport (not in the graph): dashboard push of every final,
        interims throttled to 300 ms, speaker snapshots, transcript DB write
                                                            │ finals only
                                                            ▼
               ingress queue ── batch + 3 s analysis rate limit ("analyze the latest")
                                                            │ one run at a time
                                                            ▼
   SessionGraph (thread = session_id, Postgres checkpoints, durability="async")
     apply_events ─┬─► extract_facts × long candidate utterances ─┐
                   ├─► analyze (6 dimensions) ── push to dashboard ┴─► merge ─► follow-up ─► END
                   └─► END                                             (contradictions, hint signal)

   CodeGraph (thread code:{session_id}, own queue): editor snapshots → local heuristics
     (progress, approach, stuck) → push + persist; LLM refinement runs in the background

   Beside the graphs: summary compression (one at a time, lands as an ingress event)
```

- **Memory tiers** (working memory 10, rolling summary, fact store) live in graph state; a question change resets working memory and code tracking.
- **Effects** (dashboard pushes, DB writes) are streamed by nodes and performed by the pipeline; DB writes carry idempotency keys.
- **Agentic routing** (`AGENTIC_ROUTING_ENABLED`, off by default): the analysis also chooses follow-ups (LLM contradiction check, a drafted hint shown as a "Suggested hint" card, a check of what the candidate says against their code); the rule-based contradiction pass always runs.
- **Peer interviewer** (`PEER_INTERVIEWER_ENABLED`, off by default): the copilot listens to both sides and advises the interviewer, never the candidate. It uses the same follow-up fan-out (on its own when agentic routing is off) and one dashboard channel, `send_copilot_card`, with one payload shape for every whisper; the interviewer marks a card as asked (recorded as a `CopilotCardUsed` event, exported to the report) or dismisses it, and can mute kinds.
  - *Chosen by the analysis, per candidate answer:* `suggest_probe` (1-3 follow-up questions aimed at the gaps found; also runs when the analysis reports missed key points), `verify_claim` (urgent card for a wrong or misleading technical claim, with the correction and a self-correct question), `clarify` (a reply to a candidate's unanswered question or misreading; also runs when the utterance reads as a question), `edge_cases` (inputs to trace by hand against the current code; also when the code heuristics suspect a bug; once per snapshot).
  - *Listening to the interviewer:* the latest substantial interviewer turn of a batch (at most one per 20 s) goes to `coach_interviewer` on the cheap `INTERVIEWER_COACHING` task type (leading question, revealed answer, unclear, stacked, interrupting). Talk-share (60%+ of the last 250+ words, one nudge per 5 min) and monologue (120+ words) signals need no model.
  - *The clock:* the pipeline enqueues a `Tick` every 30 s. The time steward compares the current question with its budget (the dashboard sends the remaining plan on `change_question`) and alerts once at 100% and 150%; the stuck countdown (code not moving, candidate quiet 60 s) sends a card and drafts a hint proactively, once per snapshot.
  - *After each analysis, without a model:* a rubric coverage snapshot (`send_copilot_coverage`, evidence per report criterion from the dimensions and the question's plan category; the panel names the upcoming question that fills the first gap), the adaptive-difficulty signal (three strong answers: raise the bar; two weak or two direct hint signals: step down; once per key per question), and scorecard notes (`send_scorecard`: the quote plus the insight under the criterion the question speaks to; the interviewer adds their own via `add_scorecard_note`).
  - *Ask the copilot:* `copilot_query` is answered request/response beside the graph by a tool loop (`COPILOT_QUERY`) over the live evidence index, with citations validated against the transcript.
  - *Into the report:* `report_snapshot` carries `scorecard_notes` and `copilot_cards_used`; the single-call prompt lists them, and the agentic evidence index offers `get_scorecard_notes` only when a session has notes.
- **Sessions end** by closing the pipeline and the transcription service; the session's checkpoint threads are deleted once the report has its inputs.

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

When the WebSocket connection drops, the extension stores audio chunks in **IndexedDB**. On reconnect, the `chunk-queue` drains the buffer in order, with retry logic for failed transmissions.

### 13.3 LLM Provider Failover

Automatic failover between Anthropic Claude (primary) and OpenAI GPT (fallback) based on health monitoring. Each task type has independent provider routing, so a failure in one provider doesn't affect tasks routed to another.

### 13.4 WebSocket Auto-Reconnect

The frontend `useWebSocket` hook implements exponential backoff reconnection. The dashboard WebSocket recovers session state via a catch-up mechanism that replays missed events.

### 13.5 Session State in Checkpoints

The session and code graphs checkpoint their state to Postgres after every step. A new pipeline (after a backend restart, or on another worker) first finishes any run its thread left interrupted, then continues from the checkpointed memory, facts and question context. A per-run Postgres advisory lock keeps two processes from running the same session concurrently.

`GET /api/sessions/{id}/state` builds its snapshot from those checkpoints, so any backend process serves it, current to the last run. Once a session ends its checkpoints are deleted and no snapshot is returned (the dashboard only uses it to restore a live session's question after a reload).

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

- **Google sign-in** (OIDC authorization code + PKCE), restricted to an email/domain allowlist; admins by email
- **JWT** access tokens (HS256) in memory on the frontend; refresh token in an HttpOnly, SameSite=Strict cookie scoped to `/api/auth`
- Owner-or-admin authorization on every session, plan and report (404 for others)
- The Chrome extension uses a short-lived **pairing token** scoped to one session (audio/code sockets and that session's endpoints only)

### 15.2 Data Protection

- No passwords stored (Google sign-in only)
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
- Mock LLM provider with controlled responses, and an input-sensitive **scripted** provider (deterministic, hash-derived answers; per-call-site latency; failure markers; tool use)
- **Replay goldens** (`tests/scenarios`, `tests/golden`): scripted interviews replayed through the live pipeline, recording dashboard pushes, DB writes, every LLM prompt and the final state; originally recorded from the pre-LangGraph pipeline
- Graph tests run on the real Postgres checkpointer (crash/resume with SIGKILL, idempotent writes, cross-process locking)
- Async database fixtures; opt-in live-model tests (`LIVE_LLM=1`)

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
2. **Resilience** — 3-level offline degradation, LLM failover, auto-reconnect, checkpointed session state that survives restarts
3. **Extensibility** — Interface-based LLM abstraction, modular service layer, shared type system
4. **Developer experience** — Monorepo with Turborepo, hot reload, comprehensive CI/CD, type safety across all codebases
5. **Separation of concerns** — 4-layer backend with LLM workflows as LangGraph graphs, feature-scoped frontend stores, dedicated extension workers
