"""quota_service 순수 함수 단위 테스트 (DB 불필요)."""

from datetime import datetime, timezone

from app.services.quota_service import (
    EffectiveLimits,
    check_video_duration,
    kst_day_start_utc,
    validate_poll_interval,
)

LIMITS = EffectiveLimits(
    max_groups=1, max_channels_total=5, max_analyses_per_day=10,
    max_video_minutes=60, min_poll_interval_min=60,
    plan_slug="free", plan_name="Free", has_override=False,
)


def test_kst_day_start_utc_afternoon():
    # KST 2026-07-09 14:00 = UTC 05:00 → 당일 KST 자정 = UTC 2026-07-08 15:00
    now = datetime(2026, 7, 9, 5, 0, tzinfo=timezone.utc)
    assert kst_day_start_utc(now) == datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)


def test_kst_day_start_utc_crosses_utc_date():
    # UTC 7/8 16:00 = KST 7/9 01:00 → KST 자정은 UTC 7/8 15:00 (UTC 날짜와 다름)
    now = datetime(2026, 7, 8, 16, 0, tzinfo=timezone.utc)
    assert kst_day_start_utc(now) == datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)


def test_check_video_duration():
    assert check_video_duration(LIMITS, 60 * 60) is True        # 정확히 한도
    assert check_video_duration(LIMITS, 60 * 60 + 1) is False   # 초과
    assert check_video_duration(LIMITS, None) is True           # 길이 미상은 통과
    assert check_video_duration(None, 999999) is True           # 무제한(admin/owner 없음)


def test_validate_poll_interval():
    assert validate_poll_interval(LIMITS, 60) is True
    assert validate_poll_interval(LIMITS, 59) is False
    assert validate_poll_interval(LIMITS, None) is True         # 미지정=그룹 기본값 사용
    assert validate_poll_interval(None, 1) is True
