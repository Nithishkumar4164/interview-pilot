from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.router import api_router
from src.config import settings
from src.dependencies import async_session_factory, health_monitor
from src.services.persistence_service import PersistenceService
from src.services.session_state_service import SessionStateService
from src.utils.logging import setup_logging
from src.websocket.audio_handler import router as audio_ws_router
from src.websocket.code_handler import router as code_ws_router
from src.websocket.dashboard_handler import router as dashboard_ws_router

session_state_service = SessionStateService()
persistence_service = PersistenceService(async_session_factory)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    setup_logging()

    # Start health monitoring for LLM providers
    health_monitor.start()

    # Start periodic session state snapshots
    session_state_service.start_periodic_snapshots(async_session_factory)

    # Start persistence service
    await persistence_service.start()

    yield

    # Shutdown
    await persistence_service.stop()
    health_monitor.stop()
    session_state_service.stop_periodic_snapshots()


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
