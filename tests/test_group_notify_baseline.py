"""그룹 발송 기준선 게이트 검증.

채널용 _passes_notify_baseline과 달리, baseline None이면 '보류(False)'다
(sendable인데 기준선이 비면 flood 방지를 위해 자동 발송하지 않는다).
"""

from datetime import datetime, timedelta, timezone

from app.services.monitor_service import _passes_group_baseline

BASE = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)


def test_none_baseline_blocks():
    assert _passes_group_baseline(None, BASE) is False


def test_published_after_baseline_passes():
    assert _passes_group_baseline(BASE, BASE + timedelta(hours=1)) is True


def test_published_before_baseline_blocked():
    assert _passes_group_baseline(BASE, BASE - timedelta(seconds=1)) is False


def test_equal_timestamp_passes():
    assert _passes_group_baseline(BASE, BASE) is True
