"""댓글 분석 쿼터 순수 함수 테스트 (DB 불필요)."""

import pytest

from app.services.quota_service import (
    EffectiveLimits,
    QuotaExceeded,
    check_comment_analysis_quota,
    credits_for,
    quota_verdict,
)


def _limits(*, per_month: int = 5, per_analysis: int = 1000) -> EffectiveLimits:
    return EffectiveLimits(
        max_groups=1,
        max_channels_total=10,
        max_analyses_per_day=10,
        max_video_minutes=60,
        min_poll_interval_min=10,
        plan_slug="free",
        plan_name="Free",
        has_override=False,
        monthly_cost_budget_usd=None,
        max_comment_analyses_per_month=per_month,
        max_comments_per_analysis=per_analysis,
    )


def test_credits_for_rounds_up_with_floor_of_one():
    assert credits_for(0) == 1
    assert credits_for(1) == 1
    assert credits_for(999) == 1
    assert credits_for(1000) == 1
    assert credits_for(1001) == 2
    assert credits_for(2000) == 2
    assert credits_for(5000) == 5


def test_admin_none_limits_always_pass():
    assert quota_verdict(None, used_credits=999, requested_limit=100000) is None


def test_requested_limit_over_plan_cap_is_rejected():
    msg = quota_verdict(_limits(), used_credits=0, requested_limit=2000)
    assert msg is not None
    assert "1,000건" in msg


def test_exact_cap_is_allowed():
    assert quota_verdict(_limits(), used_credits=0, requested_limit=1000) is None


def test_credits_exhausted_is_rejected():
    msg = quota_verdict(_limits(), used_credits=5, requested_limit=1000)
    assert msg is not None
    assert "5회" in msg


def test_partial_remaining_cannot_cover_weighted_cost():
    # 잔여 1회인데 2,000건(2회)을 요청 → 거절
    lim = _limits(per_month=5, per_analysis=5000)
    msg = quota_verdict(lim, used_credits=4, requested_limit=2000)
    assert msg is not None


def test_remaining_exactly_covers_weighted_cost():
    lim = _limits(per_month=5, per_analysis=5000)
    assert quota_verdict(lim, used_credits=3, requested_limit=2000) is None


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeSession:
    """execute()가 미리 정한 값을 돌려주는 최소 스텁."""

    def __init__(self, used_credits: int):
        self._used = used_credits

    async def execute(self, _stmt):
        return _FakeResult(self._used)


async def test_check_passes_for_admin(monkeypatch):
    async def fake_limits(session, user_id):
        return None

    monkeypatch.setattr(
        "app.services.quota_service.effective_limits", fake_limits
    )
    # 예외가 나지 않으면 통과
    await check_comment_analysis_quota(_FakeSession(999), user_id=1, requested_limit=100000)


async def test_check_raises_when_exhausted(monkeypatch):
    async def fake_limits(session, user_id):
        return _limits()

    monkeypatch.setattr(
        "app.services.quota_service.effective_limits", fake_limits
    )
    with pytest.raises(QuotaExceeded) as ei:
        await check_comment_analysis_quota(
            _FakeSession(5), user_id=1, requested_limit=1000
        )
    assert ei.value.limit == 5
    assert ei.value.current == 5


async def test_check_passes_when_within_limit(monkeypatch):
    async def fake_limits(session, user_id):
        return _limits()

    monkeypatch.setattr(
        "app.services.quota_service.effective_limits", fake_limits
    )
    await check_comment_analysis_quota(_FakeSession(4), user_id=1, requested_limit=1000)
