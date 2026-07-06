"""due 판정·유효값 계산 등 순수 로직 검증. SQL 실행은 E2E에서."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.channel_registry_service import (
    DueChannel,
    desired_subscription_values,
    filter_due,
)

NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


def _row(channel_id="UC1", last_polled_at=None, interval=60, window=24):
    return SimpleNamespace(
        channel_id=channel_id,
        upload_playlist_id=f"UU{channel_id[2:]}",
        last_polled_at=last_polled_at,
        interval_min=interval,
        window_hours=window,
    )


def test_never_polled_is_due():
    due = filter_due([_row(last_polled_at=None)], now=NOW, floor_min=10)
    assert [d.channel_id for d in due] == ["UC1"]


def test_not_due_within_interval():
    row = _row(last_polled_at=NOW - timedelta(minutes=30), interval=60)
    assert filter_due([row], now=NOW, floor_min=10) == []


def test_due_after_interval():
    row = _row(last_polled_at=NOW - timedelta(minutes=61), interval=60)
    assert len(filter_due([row], now=NOW, floor_min=10)) == 1


def test_floor_clamps_short_interval():
    # 구독 최솟값 1분이어도 하한 10분 미만이면 due 아님
    row = _row(last_polled_at=NOW - timedelta(minutes=5), interval=1)
    assert filter_due([row], now=NOW, floor_min=10) == []
    row2 = _row(last_polled_at=NOW - timedelta(minutes=11), interval=1)
    assert len(filter_due([row2], now=NOW, floor_min=10)) == 1


def test_naive_last_polled_treated_as_utc():
    row = _row(last_polled_at=(NOW - timedelta(minutes=61)).replace(tzinfo=None), interval=60)
    assert len(filter_due([row], now=NOW, floor_min=10)) == 1


def test_due_channel_carries_max_window():
    row = _row(last_polled_at=None, window=72)
    d = filter_due([row], now=NOW, floor_min=10)[0]
    assert d == DueChannel(
        channel_id="UC1", upload_playlist_id="UU1",
        effective_interval_min=60, fetch_window_hours=72,
    )


def test_desired_subscription_values_resolves_group_default():
    polling = SimpleNamespace(default_channel_interval_min=720, window_hours=24)
    ch_with = SimpleNamespace(channel_id="UC1", poll_interval_min=60, is_active=True)
    ch_default = SimpleNamespace(channel_id="UC2", poll_interval_min=None, is_active=True)
    ch_inactive = SimpleNamespace(channel_id="UC3", poll_interval_min=30, is_active=False)
    out = desired_subscription_values([ch_with, ch_default, ch_inactive], polling)
    # 비활성 채널은 구독 대상 아님, NULL 주기는 그룹 기본값으로 해석
    assert out == {"UC1": (60, 24), "UC2": (720, 24)}
