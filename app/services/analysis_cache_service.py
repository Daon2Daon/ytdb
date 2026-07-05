"""공유 분석 캐시 서비스 (스펙 §2.9).

선점 프로토콜: analysis_cache의 UNIQUE(video_id, preset_id, model)를 락으로 사용.
- INSERT ON CONFLICT DO NOTHING RETURNING이 성공하면 이 워커가 분석 수행권을 가진다.
- 충돌 시 기존 행 상태에 따라: completed=적중, pending(신선)=다른 워커 진행 중,
  pending(오래됨: 워커 사망 추정)/failed=조건부 UPDATE로 재클레임(rowcount로 판정).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.analysis_cache import AnalysisCache
from app.models.control.analysis_delivery import AnalysisDelivery

# pending이 이 시간을 넘기면 분석 워커가 죽은 것으로 보고 재클레임을 허용한다(스펙 §8).
CACHE_STALE_PENDING_MINUTES = 30


@dataclass(frozen=True)
class ClaimOutcome:
    kind: str  # 'hit' | 'claimed' | 'in_progress'
    cache_id: Optional[int] = None
    analysis: Optional[Dict[str, Any]] = None


async def claim_or_get(
    session: AsyncSession, video_id: str, preset_id: int, model: str
) -> ClaimOutcome:
    # 1) 선점 시도
    ins = (
        pg_insert(AnalysisCache)
        .values(video_id=video_id, preset_id=preset_id, model=model, status="pending")
        .on_conflict_do_nothing(constraint="uq_analysis_cache_key")
        .returning(AnalysisCache.cache_id)
    )
    cache_id = (await session.execute(ins)).scalar()
    if cache_id is not None:
        return ClaimOutcome(kind="claimed", cache_id=cache_id)

    # 2) 기존 행 조회
    row = (
        await session.execute(
            select(AnalysisCache).where(
                AnalysisCache.video_id == video_id,
                AnalysisCache.preset_id == preset_id,
                AnalysisCache.model == model,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # 삽입 충돌 직후 삭제된 극단 케이스 — 다음 틱에 재시도
        return ClaimOutcome(kind="in_progress")
    if row.status == "completed":
        return ClaimOutcome(kind="hit", cache_id=row.cache_id, analysis=row.analysis)

    # 3) pending(오래됨) 또는 failed → 조건부 재클레임
    reclaimable = row.status == "failed"
    if row.status == "pending":
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=CACHE_STALE_PENDING_MINUTES)
        reclaimable = created < cutoff
    if not reclaimable:
        return ClaimOutcome(kind="in_progress")

    result = await session.execute(
        update(AnalysisCache)
        .where(
            AnalysisCache.cache_id == row.cache_id,
            AnalysisCache.status == row.status,  # 상태가 그대로일 때만(동시 재클레임 방지)
            # 첫 재클레임이 created_at을 갱신하므로, 두 번째 동시 재클레임은 매치 실패한다
            # (stale-pending은 status가 pending→pending으로 안 바뀌어 status 가드만으론 부족).
            AnalysisCache.created_at == row.created_at,
        )
        .values(status="pending", created_at=datetime.now(timezone.utc), completed_at=None)
    )
    if int(result.rowcount or 0) == 1:
        return ClaimOutcome(kind="claimed", cache_id=row.cache_id)
    return ClaimOutcome(kind="in_progress")


async def mark_completed(
    session: AsyncSession,
    cache_id: int,
    analysis: Dict[str, Any],
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> None:
    await session.execute(
        update(AnalysisCache)
        .where(AnalysisCache.cache_id == cache_id)
        .values(
            status="completed",
            analysis=analysis,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            completed_at=datetime.now(timezone.utc),
        )
    )


async def mark_failed(session: AsyncSession, cache_id: int) -> None:
    await session.execute(
        update(AnalysisCache)
        .where(AnalysisCache.cache_id == cache_id, AnalysisCache.status == "pending")
        .values(status="failed")
    )


async def record_delivery(
    session: AsyncSession, user_id: int, group_id: int, cache_id: int
) -> None:
    session.add(AnalysisDelivery(user_id=user_id, group_id=group_id, cache_id=cache_id))


# ── 제어 평면 세션을 여는 편의 래퍼 (monitor_service가 사용) ──────────────────


async def claim_or_get_cached(video_id: str, preset_id: int, model: str) -> ClaimOutcome:
    async with get_sessionmaker()() as session:
        outcome = await claim_or_get(session, video_id, preset_id, model)
        await session.commit()
        return outcome


async def complete_cached(cache_id: int, analysis: Dict[str, Any]) -> None:
    async with get_sessionmaker()() as session:
        await mark_completed(session, cache_id, analysis)
        await session.commit()


async def fail_cached(cache_id: int) -> None:
    async with get_sessionmaker()() as session:
        await mark_failed(session, cache_id)
        await session.commit()


async def record_delivery_for(user_id: int, group_id: int, cache_id: int) -> None:
    async with get_sessionmaker()() as session:
        await record_delivery(session, user_id, group_id, cache_id)
        await session.commit()
