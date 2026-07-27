"""댓글 분석 쿼터 순수 함수 테스트 (DB 불필요)."""

from app.services.quota_service import EffectiveLimits, credits_for, quota_verdict


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
