"""ytdb FastAPI 진입점.

부팅 시 제어 평면(app 스키마)을 멱등 생성하고 그룹/설정 API를 노출한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings as app_settings
from app.control_db import ensure_control_schema
from app.routers import actions, channels, digests, groups, logs, settings, tags, videos
from app.services.db_engine import DBNotConfiguredError
from app.services.scheduler import shutdown_scheduler, start_scheduler

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_control_schema()
    if app_settings.SCHEDULER_ENABLED:
        start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(title="ytdb", description="다중 그룹 YouTube 모니터", lifespan=lifespan)


@app.exception_handler(DBNotConfiguredError)
async def db_not_configured_handler(_request: Request, exc: DBNotConfiguredError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(groups.router)
app.include_router(settings.router)
app.include_router(channels.router)
app.include_router(videos.router)
app.include_router(tags.router)
app.include_router(digests.router)
app.include_router(actions.router)
app.include_router(logs.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))
