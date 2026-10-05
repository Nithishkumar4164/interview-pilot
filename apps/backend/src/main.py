import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.router import api_router
from src.config import settings
from src.dependencies import async_session_factory, checkpointer, get_report_runner, health_monitor
from src.services.persistence_service import PersistenceService
from src.services.session_state_service import SessionStateService
from src.utils.logging import setup_logging
from src.utils.tracing import setup_tracing, shutdown_tracing
from src.websocket.audio_handler import router as audio_ws_router
from src.websocket.code_handler import router as code_ws_router
from src.websocket.dashboard_handler import router as dashboard_ws_router

session_state_service = SessionStateService()
persistence_service = PersistenceService(async_session_factory)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    setup_logging()
    setup_tracing()

    # Start health monitoring for LLM providers
    health_monitor.start()

    # Periodic session-state snapshots. The LangGraph pipeline checkpoints
    # session state itself, and GET /state reads it from there.
    if not settings.use_langgraph_session:
        session_state_service.start_periodic_snapshots(async_session_factory)

    # Start persistence service
    await persistence_service.start()

    # LangGraph checkpointer (creates/migrates its own tables)
    await checkpointer.open(settings.database_url_sync)

    # Finish any report whose generation was interrupted by a restart.
    resume_task = asyncio.create_task(get_report_runner().resume_pending())

    yield

    # Shutdown
    resume_task.cancel()
    await checkpointer.close()
    await persistence_service.stop()
    health_monitor.stop()
    session_state_service.stop_periodic_snapshots()
    shutdown_tracing()


app = FastAPI(
    title="InterviewPilot",
    description="AI-Powered Real-Time Interview Assistant",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
app.include_router(audio_ws_router)
app.include_router(code_ws_router)
app.include_router(dashboard_ws_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/providers")
async def provider_health() -> dict:
    return {
        "providers": {
            name: {
                "status": status.status.value,
                "consecutive_failures": status.consecutive_failures,
                "average_latency_ms": status.average_latency_ms,
            }
            for name, status in health_monitor.get_all_statuses().items()
        }
    }
