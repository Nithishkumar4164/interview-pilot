# Plan: Reimplement the LLM pipelines on LangGraph

**Goals** (all four were chosen): durable state and recovery, observability, agentic behavior, cleaner orchestration.
**Scope:** real-time analysis, report generation, plan generation, code analysis and hints.
**Fixed decisions:**
- Keep `ILLMProvider` / `FailoverManager` / `HealthMonitor`. Graph nodes call them.
- Store graph state with a Postgres checkpointer on the existing DB, using `thread_id = session_id`.

> Revision 3. Changes from draft 1 are marked **(rev)** and summarized in §8. Changes found while implementing Phase 0 are marked **(rev 3)**.

---

## 1. What exists today

| Pipeline | Code | Orchestration style |
|---|---|---|
| Real-time analysis | `workers/analysis_worker.py` plus `services/{memory,analysis,contradiction,runtime_hint}_service.py` | Callback per transcript event. A hand-rolled rate limiter (`_pending_snapshot`, `call_later`, `_analysis_lock`, `MIN_ANALYSIS_INTERVAL_S = 3`). Compression and fact extraction run as fire-and-forget `asyncio.create_task`. State lives in process memory. |
| Code analysis | `services/code_analysis_service.py`, wired in `websocket/code_handler.py` | Local heuristics, then a conditional LLM call. State is a per-session dict in the process. |
| Runtime hints / code rating | `AnalysisWorker.handle_runtime_hint_request`, `handle_code_rating` | Request/response over the dashboard WebSocket |
| Plan generation | `services/plan_service.py` | Loops sequentially over phases, then over questions for hint generation. No parallelism. |
| Report | `services/report_generator.py`, `report_service.py`, `api/sessions.py::_generate_report_background` | Build context → one Opus call → parse → fallback report on failure |
| Recovery | `services/session_state_service.py` | Snapshots `AnalysisWorker.to_dict()` plus `plan_progress`, `active_question_id`, `code_snapshot_count` into `session_snapshots` every 30 seconds; served by `GET /api/sessions/{id}/state` |
| Regression harness | `replay/` and `tests/scenarios/*.scenario.json` (**uncommitted**) | Replays scenarios through a real `AnalysisWorker` with `MockProvider` |

The replay harness is the main parity tool for this migration, so it should be committed before Phase 0.

---

## 2. Target architecture

```
                       ┌──────────────── Postgres checkpointer ───────────────────────────────────────┐
                       │                                                                              │
audio_handler ──┐      │   SessionGraph  (thread = session_id; one serialized run at a time)          │
code_handler  ──┼─► SessionIngress ──► [route_batch] ─┬─► utterances path                             │
dashboard ops ──┘   (per-session queue,               ├─► question_change path                        │
                     batch + rate-limit + serialize)  ├─► code_snapshot path                          │
                                                      └─► hint_ack / hint_delivered / memory_updated  │
                       │                                                                              │
                       └──────── stream (custom events) ──► EffectsSink ──► dashboard_manager         │
                                                                       └──► persistence_service (idempotent)

Side graphs (own threads; read a session-state snapshot; write back only via ingress events):
    MemoryGraph        thread memory:{session_id}         compression + fact extraction   (rev)
    RuntimeHintGraph   thread hint:{session_id}:{req_id}
    CodeRatingGraph    thread rating:{session_id}:{req_id}

Request-scoped graphs:
    ReportGraph        thread report:{report_id}          (rev: per attempt, not per session)
    PlanGraph          thread plan:{plan_id}
```

### 2.1 Cross-cutting design rules

1. **Nodes are pure.** A node reads state, calls the LLM through `FailoverManager`, and returns a state update. It never calls `dashboard_manager` or `safe_enqueue` directly. It emits effect events with `get_stream_writer()`, and an `EffectsSink` that consumes `astream(stream_mode=["updates","custom"])` performs them.
   - Why: when a run resumes from a checkpoint after a crash, LangGraph re-runs the interrupted node. Side effects inside nodes would then be duplicated. Keeping them in one sink puts idempotency in a single place.
   - **(rev) Stated exception:** request-scoped graphs (report, plan) end with a `persist` node that upserts by `report_id` / `plan_id`. That is an idempotent write, so it is allowed inside the graph and it makes "resume after crash" self-contained.
2. **Side effects are idempotent.** Each effect carries a deterministic key: `f"{session_id}:{utterance_id}:{kind}"`. The persistence writes behind `persistence_service` become `INSERT ... ON CONFLICT DO NOTHING` on that key. This needs a migration that adds `idempotency_key` with a unique index to `analysis_events`, `hint_events` and `transcripts`.
3. **Exactly one writer per session thread.** `SessionIngress` owns a per-session `asyncio.Queue` and runs `graph.ainvoke` calls one after another. Side graphs never write to the session thread; their results come back as ingress events. If more than one backend process ever runs, the ingress also holds a Postgres advisory lock keyed on `session_id` for the life of the session.
4. **(rev) The ingress does batching and rate limiting, not the graph.** A run's input is `events: list[IngressEvent]`, everything that queued up while the previous run was in flight. The ingress also enforces `MIN_ANALYSIS_INTERVAL_S` (3 s, reset on question change) so that a fast LLM does not turn "every utterance" into an analysis, which today's rate limiter prevents and which the parity tests depend on. Inside the graph, all utterances in the batch go to memory; analysis runs only on the latest qualifying candidate utterance, matching today's "analyze the latest" behavior.
5. **Transport stays outside the graph.** Interim transcripts, the 300 ms throttle, speaker snapshots, and the dashboard catch-up ring buffer of recent final transcripts stay in the handlers. **(rev)** The ring buffer stays in-process because `persistence_service` flushes every 2 s, so reading catch-up from the DB right after a reconnect could miss the last two seconds. Only **final** utterances enter the graph.
6. **State stays lean.** The full transcript and full analysis history are *not* in graph state, because the DB remains the source of truth for them. State keeps bounded windows: working memory (10), recent insights (N), and the last K code snapshots. Checkpoint size then stays roughly constant over a 60-minute interview.
7. **LLM calls stay on `FailoverManager`.** `FailoverManager.analyze` emits an `llm.analyze` OTel span with provider, model, task type, tokens, latency and failover (as an attribute plus a `llm.failover` event), and never prompt text. Because we are not using LangChain chat models, LangGraph's automatic LLM tracing will not see these calls. **(rev 3)** Fan-out bursts are bounded per invocation with LangGraph's `max_concurrency` config on the plan and report graphs, not a global per-provider semaphore. A global limit would also throttle the live path, where analysis, fact extraction, compression and contradiction checks share providers across sessions. Verified on 1.2.12: `max_concurrency=3` caps a 12-way `Send` fan-out at 3.
8. **(rev) Retries are layered deliberately.** `FailoverManager` already does primary → fallback. A node-level `RetryPolicy` sits on top with `max_attempts=2` and only for transient exception types (timeouts, 5xx, rate limits). Without that restriction, a deterministic parse error would run the whole failover chain twice.

### 2.2 SessionGraph state (sketch)

```python
class SessionState(TypedDict):
    session_id: str
    events: list[IngressEvent]                           # this run's batch (overwritten each run)
    # memory tiers
    working_memory: Annotated[list[Utterance], keep_last(10)]
    unsummarized: Annotated[list[Utterance], buffer_reducer]   # drained by the memory_updated event
    rolling_summary: str
    fact_store: Annotated[FactStore, merge_facts]
    # question context  (rev: everything GET /state needs lives here)
    current_question: QuestionContext | None
    current_question_index: int
    active_question_id: str | None
    plan_progress: PlanProgress
    active_hint: str
    hint_interactions: Annotated[list[dict], keep_last(50)]
    # analysis
    last_analysis: EnhancedAnalysisResult | None
    recent_insights: Annotated[list[Insight], keep_last(10)]
    contradictions: Annotated[list[Contradiction], keep_last(20)]
    planned_actions: list[Action]                        # agentic router output for this run
    # code
    code: CodeState                                      # last K snapshots, approach, progress, stuck flag, snapshot_count
    # counters
    stats: Annotated[Stats, add_stats]
```

Every existing `to_dict()` / `from_dict()` in `MemoryService`, `AnalysisService` and `ContradictionService` becomes unnecessary, because the checkpointer serializes this schema. Types stored in state need to be Pydantic models or dataclasses that the checkpointer's serde can round-trip. Phase 0 includes a test that proves this. **(rev)** `GET /api/sessions/{id}/state` keeps its current response shape via a small adapter over `aget_state()`, so the frontend does not change.

---

## 3. Graphs

### 3.1 SessionGraph: utterances path (hot path)

```
route_batch
  └─ utterances ─► append_memory ─► emit memory_maintenance_needed?   (rev: no LLM work here)
                                 └─► should_analyze? ──no──► END
                                         │yes (latest candidate utterance in batch, > 5 words)
                                         ▼
                                     analyze_utterance     (6 dimensions + next_actions)
                                         │  emits: analysis, red_flag, hint_signal effects
                                         ▼
                                     plan_actions (conditional fan-out via Send)
                                   ┌─────┼──────────────┬───────────────┐
                                   ▼     ▼              ▼               ▼
                        check_contradiction  draft_proactive_hint  inspect_code   (none → END)
                                   └─────┴──────┬───────┴───────────────┘
                                                ▼
                                             END
```

- **(rev) Memory maintenance runs in `MemoryGraph`, not in this run.** Today compression and fact extraction are background tasks and analysis uses whatever summary exists at the time. Keeping them in the session run would make every queued utterance wait on a compression call, so the port preserves today's semantics instead: `append_memory` emits a `memory_maintenance_needed` effect when `len(unsummarized) ≥ SUMMARY_TRIGGER_COUNT` or an utterance has more than 20 words; the sink launches `MemoryGraph` on `memory:{session_id}` with a snapshot of the buffer; its result comes back as a `memory_updated` ingress event that sets `rolling_summary`, drains the compressed items from `unsummarized`, and merges facts. Only one `MemoryGraph` run per session is in flight at a time, which is the same rule as `_is_compress_running()` today. A failed compression leaves the buffer untouched, so it retries on the next trigger, exactly as now.
- **Latency.** The dashboard gets the analysis as soon as `analyze_utterance` finishes, because the sink streams it out. The action nodes run after that push and do not delay it.
- **Agentic routing without extra latency.** Extend the analysis JSON schema with `next_actions: ["check_contradiction" | "draft_hint" | "inspect_code"]`. The same LLM call that scores the utterance also decides the follow-ups, and conditional edges plus `Send` dispatch them. This replaces two things:
  - the hardcoded "always run contradiction check" (the `contradictions.check` call in `analysis_worker.py`), where the rule-based prefilter stays as a guard;
  - "hint_signal → only a signal". The agent can now draft the actual hint text ahead of time.
- **Bounds.** At most one action of each kind per run. Each action node has a timeout. A failed action logs and ends; it never fails the run.
- **Feature flag.** `AGENTIC_ROUTING_ENABLED`. When it is off, `plan_actions` reproduces today's fixed behavior (always contradiction-check, never draft), which is what the parity tests rely on.

### 3.2 SessionGraph: other event paths

| Event | Path |
|---|---|
| `question_change` | emit `force_compress` for MemoryGraph → reset `working_memory`, `active_hint`, `recent_insights` → set `current_question`, `current_question_index`, `active_question_id`. Replaces `on_question_change` and `memory.reset_for_new_question`. The ingress resets its rate-limit clock on this event. |
| `code_snapshot` | `local_code_heuristics` (progress, approach, stuck detection) → `should_llm_analyze?` → `llm_code_analysis` → emit. This moves `CodeAnalysisService` into the session state, so the utterance agent's `inspect_code` can see live code state. |
| `hint_ack` | append to `hint_interactions` |
| `hint_delivered` (from RuntimeHintGraph) | set `active_hint`, so the hint_reception dimension is scored |
| `memory_updated` (from MemoryGraph) | apply summary, drain buffer, merge facts |

### 3.3 Side graphs: memory, runtime hint and code rating

Runtime hint and code rating are request/response calls with a user waiting, so they must not queue behind an analysis run.
- The flow is `graph.aget_state(session)` → run the stateless side graph (`resolve_directness` → `assemble_context` → `generate`) → return the response → enqueue an ingress event.
- They run in their own threads so they are traced and can be retried; those threads are deleted when the run completes.

### 3.4 PlanGraph

```
load_inputs ─► calibrate (profile, time allocation, phases)
           ─► Send(select_or_generate_questions, phase) × N phases      ← parallel, was sequential
           ─► Send(generate_hints, question) × M technical questions    ← parallel, was sequential
           ─► rebalance_time ─► validate_plan ─► persist
```
- `RetryPolicy` on the LLM nodes per §2.1.8. A phase whose question generation fails falls back to bank questions only, not a failed plan.
- Fan-out is bounded by `max_concurrency` in the invocation config (§2.1.7).
- Progress is streamed through `astream(stream_mode="custom")`, which the existing upload/plan SSE endpoints can forward.
- Plan edits (`swap_question`, `reorder_questions`, `remove_question`, `add_question`) stay as plain functions. They are CRUD, not orchestration.
- Agentic step (behind a flag): `select_or_generate_questions` gets the resume `fact_store` and may generate resume-targeted probes, such as verifying a claimed project.

### 3.5 ReportGraph

```
load_session_data (DB: transcripts, analyses, hints, code snapshots, contradictions, facts)
  ─► build_evidence_index         (rev: one shared, timestamp-anchored index for all criteria)
  ─► Send(score_criterion, c) × 7 criteria        ← agentic, Sonnet, read-only evidence tools
  ─► synthesize                   (rev: Opus; sees all 7 scores, writes recommendation + key moments)
  ─► validate (7 criteria present, scores in range, every citation resolves to a real timestamp)
        ──fail──► repair (1 retry) ──fail──► fallback_report
  ─► build_annotated_transcript (deterministic, existing code)
  ─► compute_hint_summary ─► persist
```
- Today the whole report is one Opus call. The agentic version scores each criterion separately with read-only evidence tools over the shared index: `search_transcript(query)`, `get_analyses(question_id)`, `get_code_timeline()`, `get_contradictions()`. Every score must cite evidence (transcript timestamps), and `validate` rejects citations that do not resolve. That makes reports auditable and gives the frontend key moments with real anchors.
- **(rev) Cost.** Seven independent Opus tool loops would cost roughly 7× today's report and could contradict each other. So per-criterion scoring uses the Sonnet mapping and one Opus `synthesize` call sees all seven results. `TaskType` gets a `REPORT_CRITERION` entry mapped to Sonnet.
- Multi-step tool loops need **native tool use**, and `ILLMProvider` has no tool-calling method today. The plan adds `ILLMProvider.invoke_with_tools(messages, tools, config)` to the Anthropic, OpenAI and Mock providers, plus a `FailoverManager.invoke_with_tools` with the same failover and health semantics. This is the only change to the LLM layer's interface.
- A flag (`REPORT_AGENTIC_SCORING`) keeps the single-call path available as a node for A/B comparison.
- **(rev)** Thread: `report:{report_id}`, one per generation attempt. Using `report:{session_id}` would make a regenerate request resume a finished thread. If the backend restarts mid-report, the background task resumes from the last checkpoint instead of starting over. Today the report is lost.

---

## 4. Infrastructure changes

| Item | Change |
|---|---|
| Dependencies | **Done.** `langgraph==1.2.12`, `langgraph-checkpoint-postgres==3.1.2`, `psycopg[binary,pool]==3.3.6` (the checkpointer uses psycopg3; the app keeps asyncpg for SQLAlchemy), `opentelemetry-exporter-otlp-proto-http`. The venv runs Python 3.14 while mypy targets 3.12; `langchain_core` warns about its pydantic-v1 shim on 3.14, which we don't use. |
| Checkpointer lifecycle | **Done.** `src/graphs/checkpointer.py` (`Checkpointer`: pool, `setup()`, `delete_threads`), opened in the lifespan, exposed as `dependencies.checkpointer` / `get_checkpointer()`. **(rev 3) Strict serde:** by default 1.2.12 deserializes unregistered custom types with a warning that this "will be blocked in a future version", and when blocked it returns a plain `dict` instead of raising. `src/graphs/serde.py` registers every state type in `CHECKPOINT_TYPES` and refuses all others now, and tests assert that recovered values are instances of their class. |
| Alembic | **Done.** `alembic_include_object` in `alembic/env.py`. Reproduced first: without it, autogenerate emits `drop_table` for all four checkpoint tables and their indexes. |
| Checkpoint write mode | Real-time graph: `durability="async"`, so checkpoint writes don't block the hot path. Report and plan graphs: `"sync"`. **(rev)** With `"async"`, a crash can lose the most recent step, so a resume may re-run one more node than expected. The idempotency keys make that harmless, and the crash test in §5.3 covers it. |
| **(rev) Resume on reconnect** | When a session's audio or dashboard socket reconnects, the ingress calls `aget_state()`; if `.next` is non-empty, it runs `ainvoke(None)` to finish the interrupted run before accepting new events. Without this step, checkpointing gives durability but not recovery. |
| **(rev) Checkpoint growth** | Rough volume: ~300 final utterances × ~4 steps ≈ 1.2k checkpoints per session. Rather than prune-to-latest, the report graph's `persist` node ends by deleting the session's checkpoint threads (`session_id`, `memory:{session_id}`). Once the report is persisted, the DB has everything the session state held. Side-graph threads are deleted on completion. A nightly job deletes threads for sessions ended more than 24 h ago as a backstop. |
| Recovery | `GET /api/sessions/{id}/state` reads `graph.aget_state()` through the shape adapter in §2.2. Retire `SessionStateService` snapshots and the 30-second loop, then drop `session_snapshots` in a later migration once nothing reads it |
| Observability | **(rev 3)** There was no existing OTel stack: the packages were dependencies but no TracerProvider or exporter was ever configured. `src/utils/tracing.py` now installs an OTLP/HTTP exporter only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; otherwise spans are no-ops and nothing leaves the process. Node spans are added per graph as they're built. **LangSmith is off by default**: traces would contain candidate transcripts and resumes (PII) and send them to a third party. It can be enabled explicitly per environment if wanted |
| Health endpoint | `/health/providers` is unchanged, since the `HealthMonitor` is kept |
| **(rev) Cut-over seam** | **Done (Phase 0).** `src/workers/session_pipeline.py` defines the `SessionPipeline` Protocol and `create_session_pipeline` factory. **(rev 3)** The call sites also reached into internals: end-session read `worker.memory` / `.analysis` / `.contradictions` directly, and `get_sv_transcripts` read `worker._transcript_history`. The Protocol therefore adds `report_snapshot()` and `get_transcripts_by_ids()`, and those call sites now use them. `replay/runner.py` still touches `_bg_analysis_task` and `_last_analysis_time`; it gets a clock/drain hook in Phase 3. |

---

## 5. Testing and verification

1. **Golden baseline first.** Before touching any code, run the three replay scenarios through the current `AnalysisWorker` with `MockProvider` and save the captured event sequences as golden files.
2. **Parity.** Point `replay/runner.py` at the LangGraph `SessionPipeline`, with the agentic flag **off**. Assert that the event types, their order per utterance, and the payloads match the golden files. Timing can differ. **(rev)** The runner injects a controllable clock so the 3 s rate limit behaves identically in both implementations.
3. **Crash and resume.** Add a scenario that cancels the process mid-run (after `analyze_utterance`, before `check_contradiction`), rebuilds the graph from the checkpointer, and resumes through the reconnect path in §4. Assert that no duplicate dashboard events or DB rows appear (checked with the idempotency keys) and that state is intact. Run it under both `durability="sync"` and `"async"`.
4. **Latency budget.** The replay harness records the time from a final utterance to the analysis push. It must stay at or below today's number on the same MockProvider latency profile. It must also record queue wait under a burst of 10 utterances in 5 seconds, and confirm that a `MemoryGraph` compression in flight does not delay the next analysis push.
5. **Real checkpointer in tests.** Integration tests use the docker-compose Postgres, not `MemorySaver`, so serde and schema problems show up.
6. **Agentic evaluation.** Extend `MockProvider` to script `next_actions` and tool calls. Then:
   - Scenario 03 (contradiction) must trigger `check_contradiction`.
   - Scenario 02 (stuck candidate) must trigger `draft_hint`.
   - Scenario 01 (strong candidate) must trigger neither.
7. **Report parity.** Existing `test_report_generator.py` cases must pass through `ReportGraph` in single-call mode. In agentic mode, assert that all 7 criteria are present, every score has at least one citation, and every citation resolves. **(rev)** Also assert the cost bound: token totals for an agentic report on scenario 01 stay under a fixed multiple of the single-call report.
8. **Plan parity.** `test_plan_service.py` must pass through `PlanGraph`, and wall-clock time for a 4-phase plan should drop.

---

## 6. Phases (each ends in a working, shippable state)

| # | Phase | Exit criteria |
|---|---|---|
| 0 | Commit the replay harness. Record golden baselines (**(rev 3)** against an input-sensitive `ScriptedProvider`, since the stock mock returns one string per task type and would pass even when the context sent to the LLM is wrong). Add deps. Wire up the checkpointer lifespan, strict serde, Alembic exclusion, OTel setup + LLM spans, `SessionPipeline` Protocol, idempotency-key migration | Golden files are committed. A trivial graph checkpoints to the dev DB and round-trips `EnhancedAnalysisResult` / `FactStore`. `AnalysisWorker` is reached only through the Protocol |
| 1 | **ReportGraph**, deterministic single-call mode, per-attempt threads, thread cleanup | Report tests pass. A report resumes after a killed process. **Done** — see §9 |
| 2 | **PlanGraph** with `Send` fan-out | Plan tests pass. SSE shows per-phase progress. Latency improves. **Done except SSE progress** — see §10 |
| 3 | **SessionGraph** utterances and question_change paths, **MemoryGraph**, `SessionIngress`, `EffectsSink`, behind `USE_LANGGRAPH_SESSION` | Replay parity, crash/resume, and latency tests pass. The factory switches to the new pipeline under the flag. **Done** — see §11 |
| 4 | Move the code_snapshot path, `RuntimeHintGraph` and `CodeRatingGraph` in. `code_handler` switches over. `/state` adapter | Code-hint tests pass. The `code_handler` per-process dict and `SessionStateService` snapshot loop are gone. **Done** (runtime-hint and code-rating graphs moved to Phase 5) — see §12 |
| 5 | Add `invoke_with_tools` to the LLM layer. Agentic routing on the hot path. Agentic report scoring with `synthesize` | Agentic scenario assertions pass. Manually compare reports from both modes on real sessions |
| 6 | Delete `AnalysisWorker`, service-level state and `SessionStateService`. Drop `session_snapshots`. Update `ARCHITECTURE.md` §4.7, §10, §11, §13.5 | No references to the old code remain. The full test suite is green |

Phases 1 and 2 are independent of each other. Phase 3 depends only on Phase 0.

---

## 7. Risks

- **Hot-path latency from checkpointing.** Mitigations: `durability="async"`, lean state, memory maintenance off the session thread, and a latency test that fails the build if it regresses.
- **Many writers on one thread.** Serialized ingress plus the advisory lock. Side graphs never write to the session thread directly.
- **Duplicated side effects on resume.** Effects only go out through the sink, and every effect carries an idempotency key.
- **Provider rate limits under fan-out.** `max_concurrency` per plan/report invocation.
- **Report cost and inconsistency in agentic mode.** Sonnet per criterion, one Opus synthesis, a cost assertion in tests, and the single-call path kept behind a flag.
- **LangGraph API churn.** Pin the version exactly. Check `durability`, `Send`, `RetryPolicy`, `get_stream_writer` and thread deletion against the pinned version's docs in Phase 0. These names come from recent LangGraph releases and haven't been checked against a specific version here.
- **Agentic behavior regressions.** Flag-gated, with a deterministic mode kept for parity tests and as a fallback.
- **PII in traces.** OTel stays in our own stack, and LangSmith is opt-in.

---

## 8. What changed in revision 2

| Problem in draft 1 | Fix |
|---|---|
| Compression and fact extraction ran inside the serialized session run, so every queued utterance would wait on a compression LLM call. That is slower than today and changes semantics. | `MemoryGraph` on its own thread, fed back via `memory_updated`. Same background semantics as today. |
| "Coalesce in the ingress" was hand-waved: the graph took a single `event`, and dropping the 3 s rate limit would change analysis frequency and break parity. | Run input is a batch. The ingress owns the rate limit with an injectable clock. |
| Checkpointing gave durability but nothing actually resumed an interrupted run. | Explicit resume-on-reconnect step (`aget_state().next` → `ainvoke(None)`). |
| `durability="async"` and "resume exactly where it stopped" were in tension. | Stated the loss window, tested under both modes. |
| `report:{session_id}` thread would make regenerate resume a finished run. | Per-attempt `report:{report_id}` thread. |
| Seven parallel Opus tool loops: ~7× cost and no cross-criterion consistency. | Sonnet per criterion, Opus `synthesize`, citation resolution in `validate`, cost assertion. |
| `RetryPolicy` on top of `FailoverManager` would retry the whole failover chain on deterministic errors. | Retry only transient exception types, max 2 attempts. |
| `Send` fan-out could burst provider rate limits. | Per-provider semaphore. |
| Prune-to-latest checkpoints was more machinery than needed. | Delete threads once the report is persisted; nightly backstop. |
| Four call sites and the replay runner talk to `AnalysisWorker` directly, so the flag cut-over would touch all of them. | `SessionPipeline` Protocol extracted in Phase 0; the flag switches a factory. |
| `GET /state` fields (`plan_progress`, `active_question_id`, `code_snapshot_count`) had no home in graph state, and the response shape was unaddressed. | Added to state; shape adapter keeps the frontend unchanged. |
| Dashboard catch-up from the DB could miss the last 2 s because persistence is batched. | Catch-up ring buffer stays in the transport layer. |
| "Nodes are pure" contradicted the `persist` nodes in the report and plan graphs. | Stated exception: idempotent upsert-by-id persist nodes are allowed in request-scoped graphs. |

---

## 9. Phase 1 notes (ReportGraph, deterministic mode)

Built as `load_inputs → generate → assemble → persist` in `src/graphs/report_graph.py`, run by `ReportRunner` on thread `report:{report_id}` with `durability="sync"`. `_generate_report_background` now delegates to it; startup calls `resume_pending()`.

Verified:
- **Parity:** from the same seeded session and scripted LLM, the graph persists the same `report_data` as `ReportService.load_inputs` + `ReportGenerator.generate`, except for `generated_at`.
- **Real crash:** before the change, `SIGKILL` during generation left the report `pending` forever (reproduced on the live server). After it, a restart resumes and completes it, and the thread is deleted.
- **In-test crash:** a child process is `SIGKILL`ed inside `persist`. The checkpoint on disk already holds `generate` and `assemble`, and resuming makes no second LLM call. Switching to `durability="exit"` fails this test, while a cancellation-based test cannot tell the two apart.

Decisions that differ from the draft plan:
- **Claims use a Postgres advisory lock, not a status value.** Production runs 2 gunicorn workers, so a request and a startup resume could run the same report at once. The UI renders nothing for an unknown report status, so a new `generating` state would make reports vanish from the list. The lock needs no schema change and is released automatically if the holder dies.
- **Cancellation is not failure.** When the task awaiting the graph is cancelled (shutdown), the report stays `pending` and resumable. LangGraph turns an exception raised *inside* a node, including `CancelledError`, into a regular error, so node code must never raise it on purpose.
- **No extra retries yet.** Parity-first: the generate node keeps today's behavior, where a failed call after provider failover gives the fallback report. Node-level `RetryPolicy` for transient errors (§2.1.8) comes with the agentic scoring work in Phase 5.
- Only reports that have a checkpoint are resumed. Reports left `pending` by the old code have none and are not touched.

---

## 10. Phase 2 notes (PlanGraph)

Built as `calibrate → Send(select_questions)×phases → plan_hints → Send(generate_hints)×technical questions → assemble` in `src/graphs/plan_graph.py`. `PlanService.generate_plan` delegates to it, so `/api/plans/create` and every other caller use it.

Verified:
- **Parity:** goldens were recorded from the sequential implementation before the change, across 5 cases (1–3 phases, with and without the question bank, with and without resume context). The graph reproduces the plans exactly and makes the same LLM calls, compared as a multiset. A mutation that drops generated hints fails 4 of the 5 cases; the bank-only case makes no hint calls.
- **Concurrency:** at most 3 LLM calls in flight for a 3-phase plan, and 2 when `plan_generation_max_concurrency=2`, measured by in-flight count rather than timing.
- **Latency with a 1 s LLM:** a 3-phase plan dropped from 6.01 s (sequential, run from a worktree at `HEAD`) to 2.39 s. `POST /api/plans/create` over HTTP took 2.03 s.

Decisions that differ from the draft plan:
- **No checkpointer.** Plan generation belongs to one HTTP request, so a resumed run after a crash would have nobody to deliver the plan to.
- **No SSE progress yet.** There is no plan-progress endpoint, and the frontend awaits the POST. Streaming needs a new endpoint plus UI work, and is a follow-up if 2 s turns out to feel slow.
- **Error behavior unchanged.** A failed question or hint call still fails the plan request, as before. The "fall back to bank questions" idea stays for Phase 5, since the API doesn't currently pass bank questions at all.

## 11. Phase 3 notes (SessionGraph)

Built as `src/graphs/session_graph.py` (the graph) and `src/workers/langgraph_session.py` (`LangGraphSessionPipeline`: transport, ingress queue, rate limit, effect sink, compression task, resume). `create_session_pipeline` returns it when `USE_LANGGRAPH_SESSION=true`; the default is still `AnalysisWorker`.

```
apply_events ─┬─► Send(extract_facts) × long final utterances ─┐
              ├─► Send(analyze)  (when the ingress chose one)  ─┴─► merge_results ─► follow_up ─► END
              └─► END
```

**Oracle first.** The Phase 0 goldens used a zero-latency scripted LLM, where a background fact extraction always finished before the analysis of the same utterance started. Against a real provider they run concurrently. The harness now gives each call site a latency (`REPLAY_LATENCY_MS`) and waits for the pipeline to go idle between events. It also captures DB writes and the `/state` snapshot. The goldens were re-recorded **from `AnalysisWorker`**. For 01–03 the only differences are analysis prompts no longer containing same-utterance facts, plus the outputs derived from them. Three scenarios were added: 04 (mid-session and question-change compression, long interviewer utterance, hints, a real contradiction), 05 (interims) and 06 (LLM failures). The service logic moved to plain functions shared by both implementations.

Verified:
- **Parity:** all 6 goldens pass through the graph on the real Postgres checkpointer. Seven graph mutations and seven ingress/durability mutations each fail at least one test.
- **Rate limit:** in real time with a 0.5 s window, the graph analyzes the same utterances as `AnalysisWorker` for a "latest wins" timeline and a 20 ms-spaced burst.
- **Latency** (utterance → analysis push, 30 ms scripted analysis): worker median 32 ms, graph median 38 ms (max 46 ms). The budget asserted in tests is a median within worker + 15 ms; the per-run advisory lock costs 0.2 ms median and the rest is graph steps. A 1 s compression in flight does not delay the next analysis.
- **Durability:** a restarted pipeline continues from the checkpoint, and reads served before its first run (runtime hints) see it. Two pipelines on one session lose no updates, because the per-run advisory lock serializes them. A child process SIGKILLed inside `follow_up` is finished on restart under both `sync` and `async` durability, with one `analysis_events` row per idempotency key.
- **End to end** (uvicorn, mock Deepgram over the real audio and dashboard WebSockets, mock LLM at 300 ms):
  - Before the kill: transcripts and analyses reached the dashboard.
  - The kill: `kill -9` 122 ms into an analysis left the checkpoint at `next=('analyze',)` with all 5 utterances in memory.
  - After restart: reconnecting logged `session_run_resuming`, finished the analysis and pushed it.
  - End state: 6 utterances and 4 analyses across three server lives, with no duplicate rows.
  - Ending the session produced a completed report.

Decisions that differ from the draft plan:
- **Fact extraction runs inside the session run**, in parallel with analysis and joined before the contradiction check, not in a memory side graph. That reproduces today's production ordering deterministically: the contradiction check sees the facts from all earlier utterances. With extraction in a side graph, facts from the previous utterance could still be in flight when the next check runs, so adjacent contradictions would be missed.
- **"MemoryGraph" is a task, not a graph.** Compression is one LLM call, and its crash safety comes from the session state: the buffer stays there until the `SummaryCompressed` event lands. A one-node graph would add a thread and nothing else.
- **Advisory locks have their own pool** (`Checkpointer.advisory_lock`, `lock_pool_size=10`). A run holds its lock while it needs saver connections to checkpoint, so a shared pool could deadlock once 10 runs are in flight. `try_advisory_lock` (reports) moved to the lock pool too. At most 10 session runs per process can be in flight at once; the 11th waits.
- **Rate-limit edges, deliberately different from `AnalysisWorker`:**
  - The old limiter stamped its clock when an analysis task started, so utterances delivered in the same event-loop tick all bypassed the window. The ingress stamps the clock when it picks the target.
  - A candidate still waiting out the window when the question changes is analyzed against its own question just before the switch. The old worker analyzed it later, against the new question.
- **Interims still enter memory** (parity). Fixing that is one of the four approved follow-ups below.

New failure mode: the live analysis path now needs Postgres (checkpoints and the lock). Persistence, reports and auth already did.

Approved follow-ups, all done. Each was fixed in both pipelines, since `AnalysisWorker` is still the default. Each reproduces the bug first in `tests/test_pipeline_fixes.py` on both implementations, and re-records the goldens from `AnalysisWorker` with the diff reviewed:
- **Self-contradiction** (`acb7a90`). A statement was checked against facts extracted from itself. Both pipelines now record which claims each recent utterance added and exclude them from that utterance's check.
- **Interviewer facts** (`e0def74`). Only candidate utterances are fact-extracted. In scenario 04 the interviewer's "On our team we used Kafka for years" had produced three false contradiction flags.
- **Interims in memory** (`f675bb7`). Only finals enter memory and the summary buffer; interims still reach the dashboard.
- **Session teardown** (this commit). Reproduced on a running server (flag off): after `POST /end`, audio on the still-open socket produced a transcript and an LLM analysis, and the transcription stream was never stopped.
  - End-session now calls `close_transcription_service`, which closes the pipeline and stops transcription. The graph pipeline's `close()` also deletes the session's checkpoint thread.
  - The audio socket of an ended session is closed, and new audio connections for it are refused.
  - The extension still retries refused connections with backoff; teaching it to stop on "Session ended" is an extension follow-up.

Known gaps carried forward: the transcript of an utterance spoken within 2 s of a crash is lost because `PersistenceService` buffers in memory (same as before). Two gunicorn workers still give each process its own `dashboard_manager`, so a dashboard and an audio socket for one session that land on different workers do not see each other. That issue predates this migration.

## 12. Phase 4 notes (code path, /state)

**Decisions (made with you, 2026-10-05):**
- Port the code path with parity first.
- Then reset code tracking per question, and enable the code-analysis LLM step off the hot path.
- Move the runtime-hint and code-rating graphs to Phase 5: each is a single LLM call answering a waiting request, so a one-node graph without a checkpointer adds nothing until they become multi-step.

**Code path.** `SessionPipeline.on_code_snapshot`; the code WebSocket hands snapshots to the session's pipeline, and `_active_analyzers` is gone.
- `AnalysisWorker` owns the `CodeAnalysisService`.
- `LangGraphSessionPipeline` runs **CodeGraph on its own thread (`code:{session_id}`) and queue**. If code shared the session queue, an editor snapshot would wait behind an utterance analysis, which takes 1–3 s against a real provider; tested at < 300 ms with a 1 s analysis in flight.
- Code-snapshot rows get a deterministic primary key, so a resumed run can't insert one twice. `code_snapshots` has no idempotency column, so this needed no migration.
- Scenario 07 (live coding over two questions) was recorded from `AnalysisWorker`, and the graph matches it.

**`/state`.** Under the flag, the snapshot is built from the session and code checkpoints, so any process can serve it and it is current. `SessionStateService` refreshed it only when an analysis ran, and the copy outside the running process was up to 30 s stale.
- **Parity:** for every scenario, the graph's snapshot equals what `SessionStateService` would hold if refreshed at the end.
- **Under the flag:** the pipeline stops writing `SessionStateService`, and the 30 s loop doesn't start.
- **After a session ends:** end-session saves the final snapshot, which `/state` serves once the checkpoints are gone.

**Bugs found and fixed:**
- **Code snapshots broke persistence batches** (`63fb724`). The handler passed the detected-approach dict into a VARCHAR column. The flush writes a batch in one transaction, so transcripts, analyses and hints queued in the same 2 s window were lost whenever the candidate's code had a recognizable approach.
- **Code tracking never reset between questions** (`a67a81b`). Question 2's first snapshot was reported as stuck because of question 1's history.
- **The code-analysis LLM step never ran** (`49b2186`). It is now enabled in the background, one call at a time, with stale results dropped.
- **Ending a session from a process that never held its pipeline** (this commit), e.g. after a restart or on the other worker. That left the checkpoints, saved no final snapshot, and gave the report no session memory. End-session now reads the checkpoints in that case and always deletes the session's threads.
- **`code_snapshot_count` was never incremented.**

**Verified on a running server** (flag on, mock LLM):
- A code snapshot produced a local push and then a refined push.
- `/state` returned the question index, both live and after a SIGKILL and restart, served by a process that never ran the pipeline.
- Ending the session from that process saved the snapshot, deleted both threads and completed the report; the code socket was then refused.

**Notes for later:**
- The mock provider's canned `CODE_ANALYSIS` response has the wrong shape for live code analysis (it matches nothing that uses this task type), so in mock mode the refined push repeats the local result.
- A test once reached the real Anthropic API through `audio_handler`'s import-time failover manager; it is now patched. The provider answered `404 not_found_error` for the configured model, which suggests a default model in `src/llm/config.py` is no longer available. Not verified which one.
