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
