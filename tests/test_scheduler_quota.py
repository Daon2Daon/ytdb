"""스케줄러 경로 쿼터 게이트 단위 테스트 (monkeypatch, DB 불필요)."""

from app.services.quota_service import EffectiveLimits

FREE = EffectiveLimits(
    max_groups=1, max_channels_total=5, max_analyses_per_day=10,
    max_video_minutes=60, min_poll_interval_min=60,
    plan_slug="free", plan_name="Free", has_override=False,
)


def test_video_duration_gate_skips_over_limit():
    """duration 초과 영상은 분석 진입 전 skip 판정."""
    from app.services.quota_service import check_video_duration

    assert check_video_duration(FREE, 61 * 60) is False
    assert check_video_duration(FREE, 59 * 60) is True
    assert check_video_duration(None, 10**9) is True  # admin/owner 없음 그룹


async def test_daily_quota_gate_blocks(monkeypatch):
    """일일 한도 도달 시 _daily_quota_ok가 False + 사유 반환."""
    from app.services import monitor_service as ms

    class _G:
        group_id = 10
        slug = "g1"
        owner_user_id = 2

    async def _limits(group):
        return FREE

    async def _count(session, user_id):
        return 10  # 한도와 동일 → 초과

    monkeypatch.setattr(ms, "limits_for_group_owner", _limits)
    monkeypatch.setattr(ms, "count_daily_deliveries", _count)

    ok, reason = await ms._daily_quota_ok(_G())
    assert ok is False
    assert "일일 분석 한도" in reason


async def test_daily_quota_gate_unlimited_owner(monkeypatch):
    from app.services import monitor_service as ms

    class _G:
        group_id = 10
        slug = "g1"
        owner_user_id = None

    async def _limits(group):
        return None

    monkeypatch.setattr(ms, "limits_for_group_owner", _limits)
    ok, reason = await ms._daily_quota_ok(_G())
    assert ok is True and reason == ""
