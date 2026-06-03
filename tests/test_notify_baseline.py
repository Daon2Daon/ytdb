"""채널 알림 기준 시점(notify_from) 게이트 검증.

published_at >= notify_from 인 영상만 자동 발송. notify_from None이면 전부 발송.
"""

from datetime import datetime, timedelta, timezone

from app.services.monitor_service import _passes_notify_baseline

BASE = datetime(2026, 6, 3, 14, 0, tzinfo=timezone.utc)


def test_none_baseline_notifies_all():
    assert _passes_notify_baseline(None, datetime(2020, 1, 1, tzinfo=timezone.utc)) is True


def test_published_after_baseline_passes():
    assert _passes_notify_baseline(BASE, BASE + timedelta(hours=1)) is True


def test_published_before_baseline_blocked():
    assert _passes_notify_baseline(BASE, BASE - timedelta(seconds=1)) is False


def test_equal_timestamp_passes():
    assert _passes_notify_baseline(BASE, BASE) is True
