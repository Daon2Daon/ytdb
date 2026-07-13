"""YouTube 쿼터 원장 서비스 (스펙 D-2 §1).

- key_fingerprint: 키 원문 대신 SHA-256 앞 12자만 저장(유출 면적 0).
- pt_today: Google 쿼터 리셋(PT 자정) 기준 날짜. DST는 zoneinfo가 처리.
- make_recorder: 호출마다 즉시 UPSERT. 기록 실패는 삼킨다 —
  원장 장애가 폴링/분석을 절대 깨뜨리지 않는다(ai_usage 패턴).
- 게이트 판정(system_gate_state)은 후속 태스크에서 추가.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.yt_quota_usage import YtQuotaUsage

QuotaRecorder = Callable[[int], Awaitable[None]]

_PT = ZoneInfo("America/Los_Angeles")


def key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def pt_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(_PT).date()


async def record_units(session: AsyncSession, key_fp: str, units: int) -> None:
    """(오늘PT, key_fp) 행에 units 누적 UPSERT. 커밋은 호출부 책임."""
    stmt = pg_insert(YtQuotaUsage).values(
        usage_date=pt_today(), key_fp=key_fp, units=units
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[YtQuotaUsage.usage_date, YtQuotaUsage.key_fp],
        set_={"units": YtQuotaUsage.units + stmt.excluded.units, "updated_at": func.now()},
    )
    await session.execute(stmt)


def make_recorder(api_key: str) -> QuotaRecorder:
    """YouTubeAPIClient에 주입할 best-effort recorder."""
    fp = key_fingerprint(api_key)

    async def _rec(units: int) -> None:
        try:
            sf = get_sessionmaker()
            async with sf() as session:
                async with session.begin():
                    await record_units(session, fp, units)
        except Exception as e:  # noqa: BLE001 — 스펙 D5: 원장 실패는 호출을 안 깨뜨림
            print(f"[yt-quota] 기록 실패(무시): {e}")

    return _rec


async def units_today(session: AsyncSession, key_fp: str) -> int:
    row = (
        await session.execute(
            select(YtQuotaUsage.units).where(
                YtQuotaUsage.usage_date == pt_today(), YtQuotaUsage.key_fp == key_fp
            )
        )
    ).scalar_one_or_none()
    return int(row or 0)
