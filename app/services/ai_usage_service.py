"""AI 사용량 원장 서비스 (Phase C): 기록·단가 환산·월 예산 검사의 단일 소유 지점.

귀속 원칙(스펙 §2.4): 캐시 미스 분석은 user_id=NULL(시스템 몫), 다이제스트·
직접/커스텀 프롬프트 분석은 그룹 owner 몫. 월 경계는 KST 달력 월 —
Phase B의 KST 일일 경계와 일관.
기록은 best-effort: 실패해도 분석/다이제스트를 깨뜨리지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


class BudgetExceeded(Exception):
    """월 예산 초과. 라우터는 400, 스케줄러/틱은 skip+job log로 변환한다."""

    def __init__(self, detail: str, *, limit: float, current: float) -> None:
        super().__init__(detail)
        self.detail = detail
        self.limit = limit
        self.current = current


def kst_month_start_utc(now: datetime) -> datetime:
    """now가 속한 KST 달력 월의 1일 00:00(KST)을 UTC로 반환."""
    kst_now = now.astimezone(KST)
    start = kst_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def compute_cost_usd(
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    prices: dict,
) -> Optional[Decimal]:
    """단가표(모델 prefix → {input, output} $/1M) 기반 비용. 계산 불가면 None.

    최장 prefix 매칭 — "gemini/gemini-2.5-flash"가 "gemini/"보다 우선.
    None 반환 = cost_usd NULL 기록 → 관리자 대시보드 경고로 표면화(스펙 §2.4).
    """
    if input_tokens is None or output_tokens is None or not prices:
        return None
    best = None
    for prefix in prices:
        if model.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    if best is None:
        return None
    entry = prices[best]
    try:
        return (
            Decimal(str(entry["input"])) * input_tokens
            + Decimal(str(entry["output"])) * output_tokens
        ) / Decimal(1_000_000)
    except (KeyError, TypeError, InvalidOperation):
        return None


from sqlalchemy import func as sa_func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.ai_usage import AIUsage
from app.services.quota_service import effective_limits


async def record_usage(
    *,
    user_id: Optional[int],
    group_id: Optional[int],
    purpose: str,
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    video_pk: Optional[int] = None,
) -> None:
    """원장 1행 기록 + 단가 환산. best-effort — 모든 예외를 삼킨다."""
    try:
        from app.services.global_settings import get_ai_model_prices

        prices = await get_ai_model_prices()
        cost = compute_cost_usd(model, input_tokens, output_tokens, prices)
        async with get_sessionmaker()() as session:
            async with session.begin():
                await session.execute(
                    pg_insert(AIUsage).values(
                        user_id=user_id,
                        group_id=group_id,
                        purpose=purpose,
                        model=model,
                        input_tokens=input_tokens or 0,
                        output_tokens=output_tokens or 0,
                        cost_usd=cost,
                        video_pk=video_pk,
                    )
                )
    except Exception as e:  # noqa: BLE001 — 원장 실패가 본 작업을 깨뜨리면 안 됨
        print(f"[ai_usage] 원장 기록 실패(무시): {e}")


async def month_cost_usd(session: AsyncSession, user_id: int) -> Decimal:
    """당월(KST) 본인 귀속 비용 합. cost_usd NULL 행은 0 취급."""
    since = kst_month_start_utc(datetime.now(timezone.utc))
    return (
        await session.execute(
            select(sa_func.coalesce(sa_func.sum(AIUsage.cost_usd), 0))
            .where(AIUsage.user_id == user_id, AIUsage.created_at >= since)
        )
    ).scalar_one()


async def check_monthly_budget(session: AsyncSession, user_id: int) -> None:
    """월 예산 검사. admin/예산 미설정은 통과. 초과 시 BudgetExceeded."""
    limits = await effective_limits(session, user_id)
    if limits is None or limits.monthly_cost_budget_usd is None:
        return
    current = float(await month_cost_usd(session, user_id))
    if current >= limits.monthly_cost_budget_usd:
        raise BudgetExceeded(
            f"월 AI 예산 초과: 당월 ${current:.4f} / 예산 "
            f"${limits.monthly_cost_budget_usd:.2f} (KST 월초 초기화)",
            limit=limits.monthly_cost_budget_usd,
            current=current,
        )


async def budget_ok_for_group(group) -> tuple[bool, str]:
    """스케줄러/틱용: 그룹 owner 예산 검사. (통과 여부, 초과 사유)."""
    if group.owner_user_id is None:
        return True, ""
    async with get_sessionmaker()() as session:
        try:
            await check_monthly_budget(session, group.owner_user_id)
        except BudgetExceeded as e:
            return False, e.detail
    return True, ""
