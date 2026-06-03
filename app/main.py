"""ytdb FastAPI 진입점.

부팅 시 제어 평면(app 스키마)을 멱등 생성하고 그룹/설정 API를 노출한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
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
async def meta_health() -> dict:
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


UI_DIR = STATIC_DIR / "ui"


def _serve_react() -> Response:
    """React SPA index.html 서빙(미빌드 시 503)."""
    index_file = UI_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse(
            status_code=503,
            content={"detail": "UI가 아직 빌드되지 않았습니다. frontend에서 npm run build 후 사용하세요."},
        )
    return FileResponse(str(index_file))


@app.get("/legacy", include_in_schema=False)
async def legacy_index() -> FileResponse:
    """구 vanilla UI(컷오버 롤백용)."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/", include_in_schema=False)
async def index() -> Response:
    return _serve_react()


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str) -> Response:
    """클라이언트 라우팅 경로(/g/... 등)는 모두 React index.html로 폴백한다.
    /api·/static·/health·/legacy 는 위 라우트/마운트가 먼저 처리하므로,
    여기 도달한 api/static/health/legacy 경로는 매칭 실패로 보고 404를 반환한다."""
    if (
        full_path.startswith("api")
        or full_path.startswith("static")
        or full_path == "health"
        or full_path.startswith("legacy")
    ):
        raise HTTPException(status_code=404)
    return _serve_react()
