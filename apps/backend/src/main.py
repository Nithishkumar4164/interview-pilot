from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings
from src.api.router import api_router
from src.dependencies import async_session_factory
from src.llm import FailoverManager, HealthMonitor
from src.services.session_state_service import SessionStateService
from src.utils.logging import setup_logging

# Global singletons (initialized in lifespan)
health_monitor = HealthMonitor()
failover_manager = FailoverManager(health_monitor)
session_state_service = SessionStateService()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    setup_logging()

    # Start health monitoring for LLM providers
    health_monitor.start()

    # Start periodic session state snapshots
    session_state_service.start_periodic_snapshots(async_session_factory)

    yield

    # Shutdown
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


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/providers")
async def provider_health() -> dict:
    return {"providers": {
        name: {
            "status": status.status.value,
            "consecutive_failures": status.consecutive_failures,
            "average_latency_ms": status.average_latency_ms,
        }
        for name, status in health_monitor.get_all_statuses().items()
    }}
