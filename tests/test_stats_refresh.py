"""조회수·좋아요 주기 갱신 검증."""

from app.services.settings_types import PollingSettings


def test_polling_stats_refresh_default():
    assert PollingSettings().stats_refresh_days == 30
