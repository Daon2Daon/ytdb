"""notification 설정에서 notify_baseline_at(UTC ISO) 파싱 검증."""

from datetime import datetime, timezone

from app.services.settings_manager import _as_dt


def test_as_dt_none_and_empty():
    assert _as_dt(None) is None
    assert _as_dt("") is None


def test_as_dt_parses_iso_utc():
    assert _as_dt("2026-06-06T12:00:00+00:00") == datetime(
        2026, 6, 6, 12, 0, tzinfo=timezone.utc
    )


def test_as_dt_naive_treated_as_utc():
    assert _as_dt("2026-06-06T12:00:00") == datetime(
        2026, 6, 6, 12, 0, tzinfo=timezone.utc
    )


def test_as_dt_invalid_returns_none():
    assert _as_dt("not-a-date") is None
