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
from app.routers import actions, channels, digests, groups, health, logs, settings, stats, tags, videos
from app.services.db_engine import DBNotConfiguredError
from app.services.scheduler import apply_pending_analysis_schedule, shutdown_scheduler, start_scheduler

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_control_schema()
    if app_settings.SCHEDULER_ENABLED:
        start_scheduler()
        await apply_pending_analysis_schedule()
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
app.include_router(stats.router)
app.include_router(health.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


UI_DIR = STATIC_DIR / "ui"


@app.get("/app", include_in_schema=False)
@app.get("/app/{spa_path:path}", include_in_schema=False)
async def spa(spa_path: str = "") -> FileResponse:
    """React SPA 진입점. 정적 자산은 /static/ui/ 로 로드되고,
    클라이언트 라우팅 경로(/app/...)는 모두 index.html로 폴백한다."""
    index_file = UI_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"detail": "UI가 아직 빌드되지 않았습니다. frontend에서 npm run build 후 사용하세요."},
        )
    return FileResponse(str(index_file))
