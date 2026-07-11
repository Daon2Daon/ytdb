"""ai_usage_service 순수 함수 단위 테스트 (DB 불필요)."""

from datetime import datetime, timezone
from decimal import Decimal

from app.services.ai_usage_service import (
    BudgetExceeded,
    compute_cost_usd,
    kst_month_start_utc,
)

PRICES = {
    "gemini/": {"input": 0.10, "output": 0.40},
    "gemini/gemini-2.5-flash": {"input": 0.30, "output": 2.50},
}


def test_kst_month_start_utc():
    # KST 2026-07-15 10:00 = UTC 01:00 → 7/1 00:00 KST = UTC 6/30 15:00
    now = datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc)
    assert kst_month_start_utc(now) == datetime(2026, 6, 30, 15, 0, tzinfo=timezone.utc)


def test_kst_month_start_utc_crosses_month():
    # UTC 6/30 16:00 = KST 7/1 01:00 → KST 월초는 UTC 6/30 15:00 (UTC 달과 다름)
    now = datetime(2026, 6, 30, 16, 0, tzinfo=timezone.utc)
    assert kst_month_start_utc(now) == datetime(2026, 6, 30, 15, 0, tzinfo=timezone.utc)


def test_compute_cost_longest_prefix_wins():
    # 2.5-flash는 더 긴 prefix 단가 적용: (1M×0.30 + 1M×2.50)/1M ... 토큰 1_000_000씩
    cost = compute_cost_usd("gemini/gemini-2.5-flash", 1_000_000, 1_000_000, PRICES)
    assert cost == Decimal("2.80")
    # 다른 gemini 모델은 짧은 prefix로 폴백
    cost2 = compute_cost_usd("gemini/gemini-3.1-flash-lite", 1_000_000, 1_000_000, PRICES)
    assert cost2 == Decimal("0.50")


def test_compute_cost_unknown_model_or_tokens_none():
    assert compute_cost_usd("gpt-4o", 100, 100, PRICES) is None
    assert compute_cost_usd("gemini/x", None, 100, PRICES) is None
    assert compute_cost_usd("gemini/x", 100, None, PRICES) is None
    assert compute_cost_usd("gemini/x", 100, 100, {}) is None


def test_compute_cost_malformed_price_entry():
    assert compute_cost_usd("bad/x", 100, 100, {"bad/": {"input": "oops"}}) is None


def test_budget_exceeded_detail():
    exc = BudgetExceeded("월 AI 예산 초과", limit=5.0, current=5.2)
    assert exc.limit == 5.0 and exc.current == 5.2
    assert "월 AI 예산 초과" in str(exc)


import pytest

from app.services.ai_usage_service import check_monthly_budget, record_usage


async def test_record_usage_swallows_errors(monkeypatch):
    """원장 기록 실패는 분석을 깨뜨리지 않는다 — 예외를 삼키고 경고만."""
    from app.services import ai_usage_service as aus

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(aus, "get_sessionmaker", _boom)
    # 예외가 밖으로 새면 테스트 실패
    await record_usage(
        user_id=None, group_id=1, purpose="analysis", model="m",
        input_tokens=10, output_tokens=20,
    )


async def test_check_monthly_budget_exceeded(monkeypatch):
    from app.services import ai_usage_service as aus
    from app.services.quota_service import EffectiveLimits

    LIMITS = EffectiveLimits(
        max_groups=1, max_channels_total=5, max_analyses_per_day=10,
        max_video_minutes=60, min_poll_interval_min=60,
        plan_slug="free", plan_name="Free", has_override=False,
        monthly_cost_budget_usd=5.0,
    )

    async def _limits(session, user_id):
        return LIMITS

    async def _cost(session, user_id):
        from decimal import Decimal
        return Decimal("5.1")

    monkeypatch.setattr(aus, "effective_limits", _limits)
    monkeypatch.setattr(aus, "month_cost_usd", _cost)
    with pytest.raises(aus.BudgetExceeded) as ei:
        await check_monthly_budget(None, user_id=2)
    assert "월 AI 예산 초과" in ei.value.detail


async def test_check_monthly_budget_unlimited(monkeypatch):
    from app.services import ai_usage_service as aus

    async def _none(session, user_id):
        return None

    monkeypatch.setattr(aus, "effective_limits", _none)
    await check_monthly_budget(None, user_id=1)  # admin — 통과
