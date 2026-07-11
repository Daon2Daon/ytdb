"""쿼터 서비스 (Phase B): 유효 한도 해석 + 검사 함수의 단일 소유 지점.

유효 한도 = COALESCE(user_limits.값, plan.값). admin/owner 없음/개발 모드는
limits=None으로 표현하며 모든 검사가 무조건 통과한다.
"당일" 기준은 KST(Asia/Seoul) 자정 — created_at 범위 비교로 기존
(user_id, created_at) 인덱스를 그대로 탄다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.analysis_delivery import AnalysisDelivery
from app.models.control.channel_subscription import ChannelSubscription
from app.models.control.group import Group
from app.models.control.plan import Plan
from app.models.control.user import User
from app.models.control.user_limit import UserLimit

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class EffectiveLimits:
    max_groups: int
    max_channels_total: int
    max_analyses_per_day: int
    max_video_minutes: int
    min_poll_interval_min: int
    plan_slug: str
    plan_name: str
    has_override: bool
    monthly_cost_budget_usd: Optional[float] = None  # None = 예산 무제한


def kst_day_start_utc(now: datetime) -> datetime:
    """now가 속한 KST 날짜의 자정(00:00 KST)을 UTC로 반환."""
    kst_now = now.astimezone(KST)
    start = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def check_video_duration(
    limits: Optional[EffectiveLimits], duration_seconds: Optional[int]
) -> bool:
    if limits is None or duration_seconds is None:
        return True
    return duration_seconds <= limits.max_video_minutes * 60


def validate_poll_interval(
    limits: Optional[EffectiveLimits], interval_min: Optional[int]
) -> bool:
    if limits is None or interval_min is None:
        return True
    return interval_min >= limits.min_poll_interval_min


class QuotaExceeded(Exception):
    """쿼터 초과. 라우터는 400, 스케줄러는 skip+job log로 변환한다."""

    def __init__(self, detail: str, *, limit: int, current: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.limit = limit
        self.current = current


def _merge_limits(plan: Plan, override: Optional[UserLimit]) -> EffectiveLimits:
    def pick(field: str) -> int:
        if override is not None:
            v = getattr(override, field)
            if v is not None:
                return int(v)
        return int(getattr(plan, field))

    def pick_budget() -> Optional[float]:
        if override is not None and override.monthly_cost_budget_usd is not None:
            return float(override.monthly_cost_budget_usd)
        v = plan.monthly_cost_budget_usd
        return float(v) if v is not None else None

    return EffectiveLimits(
        max_groups=pick("max_groups"),
        max_channels_total=pick("max_channels_total"),
        max_analyses_per_day=pick("max_analyses_per_day"),
        max_video_minutes=pick("max_video_minutes"),
        min_poll_interval_min=pick("min_poll_interval_min"),
        plan_slug=plan.slug,
        plan_name=plan.name,
        has_override=override is not None,
        monthly_cost_budget_usd=pick_budget(),
    )


async def effective_limits(session: AsyncSession, user_id: int) -> Optional[EffectiveLimits]:
    """유효 한도. admin/미존재 사용자는 None(무제한)."""
    row = (
        await session.execute(
            select(User, Plan, UserLimit)
            .join(Plan, Plan.plan_id == User.plan_id)
            .outerjoin(UserLimit, UserLimit.user_id == User.user_id)
            .where(User.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        return None
    user, plan, override = row
    if user.role == "admin":
        return None
    return _merge_limits(plan, override)


async def limits_for_group_owner(group: Group) -> Optional[EffectiveLimits]:
    """스케줄러/그룹 스코프 라우터용: 그룹 owner 기준 한도. None=무제한."""
    if group.owner_user_id is None:
        return None
    async with get_sessionmaker()() as session:
        return await effective_limits(session, group.owner_user_id)


# ── 현재 사용량 집계 ─────────────────────────────────────────────────────────


async def count_owned_groups(session: AsyncSession, user_id: int) -> int:
    return (
        await session.execute(
            select(sa_func.count()).select_from(Group).where(Group.owner_user_id == user_id)
        )
    ).scalar_one()


async def count_owned_channels(session: AsyncSession, user_id: int) -> int:
    """사용자 소유 전 그룹의 채널 합계 — channel_subscriptions 역방향 매핑 사용
    (그룹 스키마 순회 없이 제어 평면 쿼리 1회, B-0b 재사용)."""
    return (
        await session.execute(
            select(sa_func.count())
            .select_from(ChannelSubscription)
            .join(Group, Group.group_id == ChannelSubscription.group_id)
            .where(Group.owner_user_id == user_id)
        )
    ).scalar_one()


async def count_daily_deliveries(session: AsyncSession, user_id: int) -> int:
    from datetime import datetime as _dt

    since = kst_day_start_utc(_dt.now(timezone.utc))
    return (
        await session.execute(
            select(sa_func.count())
            .select_from(AnalysisDelivery)
            .where(AnalysisDelivery.user_id == user_id, AnalysisDelivery.created_at >= since)
        )
    ).scalar_one()


# ── 검사 함수 (초과 시 QuotaExceeded) ────────────────────────────────────────


async def check_group_quota(session: AsyncSession, user_id: int) -> None:
    limits = await effective_limits(session, user_id)
    if limits is None:
        return
    current = await count_owned_groups(session, user_id)
    if current >= limits.max_groups:
        raise QuotaExceeded(
            f"그룹 한도 초과: 현재 {current}개 / 한도 {limits.max_groups}개",
            limit=limits.max_groups, current=current,
        )


async def check_channel_quota(session: AsyncSession, user_id: int) -> None:
    limits = await effective_limits(session, user_id)
    if limits is None:
        return
    current = await count_owned_channels(session, user_id)
    if current >= limits.max_channels_total:
        raise QuotaExceeded(
            f"채널 한도 초과: 현재 {current}개 / 한도 {limits.max_channels_total}개",
            limit=limits.max_channels_total, current=current,
        )


async def check_daily_analysis_quota(session: AsyncSession, user_id: int) -> None:
    limits = await effective_limits(session, user_id)
    if limits is None:
        return
    current = await count_daily_deliveries(session, user_id)
    if current >= limits.max_analyses_per_day:
        raise QuotaExceeded(
            f"일일 분석 한도 초과: 오늘 {current}건 / 한도 {limits.max_analyses_per_day}건 "
            "(KST 자정에 초기화)",
            limit=limits.max_analyses_per_day, current=current,
        )
