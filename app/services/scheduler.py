"""APScheduler 기반 전역 잡 스케줄러.

AsyncIOScheduler를 FastAPI 이벤트 루프에서 실행해 데이터 평면 공유 풀을
재사용한다(잡마다 새 루프/풀을 만들지 않는다). 잡 본문은 활성 그룹을 순회한다.
jobstore는 메모리이며, 부팅 시 setup으로 재등록한다.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.services.digest_service import run_digest_tick_once
from app.services.monitor_service import run_master_poll_once, run_pending_analysis_once

JOB_MASTER_POLL = "youtube_master_poll"
JOB_PENDING_ANALYSIS = "youtube_pending_analysis"
JOB_DIGEST_TICK = "youtube_digest_tick"

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


def setup_jobs() -> AsyncIOScheduler:
    scheduler = get_scheduler()
    scheduler.add_job(
        run_master_poll_once,
        trigger="interval",
        minutes=int(settings.MASTER_POLL_INTERVAL_MIN),
        id=JOB_MASTER_POLL,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_pending_analysis_once,
        trigger="interval",
        minutes=int(settings.PENDING_ANALYSIS_INTERVAL_MIN),
        id=JOB_PENDING_ANALYSIS,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_digest_tick_once,
        trigger="interval",
        minutes=1,
        id=JOB_DIGEST_TICK,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def start_scheduler() -> None:
    scheduler = setup_jobs()
    if not scheduler.running:
        scheduler.start()


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
