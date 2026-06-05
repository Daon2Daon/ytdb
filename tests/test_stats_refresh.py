"""조회수·좋아요 주기 갱신 검증."""

from app.services.settings_types import PollingSettings


def test_polling_stats_refresh_default():
    assert PollingSettings().stats_refresh_days == 30


from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from app.services.monitor_service import _stats_window_cutoff, _build_stats_map


def test_stats_window_cutoff():
    now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    assert _stats_window_cutoff(now, 30) == now - timedelta(days=30)
    assert _stats_window_cutoff(now, 1) == now - timedelta(days=1)


def test_build_stats_map():
    metas = [
        SimpleNamespace(video_id="a", view_count=100, like_count=10),
        SimpleNamespace(video_id="b", view_count=None, like_count=5),
    ]
    m = _build_stats_map(metas)
    assert m == {"a": (100, 10), "b": (None, 5)}


def test_build_stats_map_empty():
    assert _build_stats_map([]) == {}
