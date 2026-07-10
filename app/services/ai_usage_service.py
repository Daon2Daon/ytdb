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
