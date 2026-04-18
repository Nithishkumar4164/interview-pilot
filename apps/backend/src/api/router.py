from fastapi import APIRouter

from src.api.auth import router as auth_router
from src.api.history import router as history_router
from src.api.plans import router as plans_router
from src.api.reports import router as reports_router
from src.api.sessions import router as sessions_router
from src.api.upload import router as upload_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(upload_router)
api_router.include_router(plans_router)
api_router.include_router(reports_router)
api_router.include_router(sessions_router)
api_router.include_router(history_router)


@api_router.get("/status")
async def api_status() -> dict[str, str]:
    return {"status": "operational", "version": "0.1.0"}
