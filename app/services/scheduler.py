"""APScheduler 기반 전역 잡 스케줄러.

AsyncIOScheduler를 FastAPI 이벤트 루프에서 실행해 데이터 평면 공유 풀을
재사용한다(잡마다 새 루프/풀을 만들지 않는다). 잡 본문은 활성 그룹을 순회한다.
jobstore는 메모리이며, 부팅 시 setup으로 재등록한다.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import settings
from app.control_db import get_sessionmaker
from app.models.control.group import Group
from app.services.central_poller import run_central_poll_once
from app.services.digest_service import run_digest_tick_once
from app.services.monitor_service import (
    run_pending_analysis_once,
    run_stats_refresh_once,
)
from app.services.notify_service import run_notify_tick_once
from app.services.plan_expiry_service import run_plan_expiry_once
from app.services.settings_manager import get_settings_manager

_MIN_ANALYSIS_INTERVAL_MIN = 1
_MAX_ANALYSIS_INTERVAL_MIN = 1440

JOB_MASTER_POLL = "youtube_master_poll"
JOB_PENDING_ANALYSIS = "youtube_pending_analysis"
JOB_DIGEST_TICK = "youtube_digest_tick"
JOB_NOTIFY_TICK = "youtube_notify_tick"
JOB_STATS_REFRESH = "youtube_stats_refresh"
JOB_PLAN_EXPIRY = "plan_expiry"

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


def _clamp_analysis_interval_minutes(value: int) -> int:
    return max(_MIN_ANALYSIS_INTERVAL_MIN, min(_MAX_ANALYSIS_INTERVAL_MIN, int(value)))


async def get_effective_pending_analysis_interval_min() -> int:
    """활성 그룹의 Monitoring(polling) 설정에서 AI 분석 주기(분)를 읽는다.

    그룹이 여러 개면 가장 짧은 주기를 사용해 전역 스케줄러 틱이 늦지 않게 한다.
    활성 그룹이 없거나 설정이 비어 있으면 .env 기본값(PENDING_ANALYSIS_INTERVAL_MIN)을 쓴다.
    """
    fallback = _clamp_analysis_interval_minutes(settings.PENDING_ANALYSIS_INTERVAL_MIN)
    sf = get_sessionmaker()
    async with sf() as session:
        groups = list(
            (await session.execute(select(Group).where(Group.is_active.is_(True)))).scalars().all()
        )
    if not groups:
        return fallback

    mgr = get_settings_manager()
    intervals: list[int] = []
    for group in groups:
        polling = await mgr.get_polling(group.group_id)
        intervals.append(_clamp_analysis_interval_minutes(polling.pending_analysis_interval_min))
    return min(intervals)


async def apply_pending_analysis_schedule() -> None:
    """DB에 저장된 AI 분석 주기로 pending 분석 잡 간격을 갱신한다."""
    minutes = await get_effective_pending_analysis_interval_min()
    scheduler = get_scheduler()
    job = scheduler.get_job(JOB_PENDING_ANALYSIS)
    if job is None:
        scheduler.add_job(
            run_pending_analysis_once,
            trigger="interval",
            minutes=minutes,
            id=JOB_PENDING_ANALYSIS,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    else:
        scheduler.reschedule_job(JOB_PENDING_ANALYSIS, trigger="interval", minutes=minutes)


def setup_jobs() -> AsyncIOScheduler:
    scheduler = get_scheduler()
    scheduler.add_job(
        run_central_poll_once,       # B-0b: 그룹 순회 폴링 → 중앙 폴링
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
    scheduler.add_job(
        run_notify_tick_once,
        trigger="interval",
        minutes=1,
        id=JOB_NOTIFY_TICK,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_stats_refresh_once,
        trigger="interval",
        minutes=1440,
        id=JOB_STATS_REFRESH,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_plan_expiry_once,        # E-1: 유료 플랜 만료 강등·임박 알림
        trigger="interval",
        minutes=30,
        id=JOB_PLAN_EXPIRY,
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
