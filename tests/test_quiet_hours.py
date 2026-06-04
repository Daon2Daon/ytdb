"""야간(지정 시간대) 발송 제한 판정. 타임존 인자화."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.quiet_hours import is_in_quiet_hours, is_quiet_hours_now

KST = ZoneInfo("Asia/Seoul")


def _at(h: int, m: int) -> datetime:
    return datetime(2026, 6, 4, h, m, tzinfo=KST)


def test_same_day_window():
    # 09:00~17:00 → 12:00 제한, 08:00 비제한
    assert is_in_quiet_hours("09:00", "17:00", tz=KST, now=_at(12, 0)) is True
    assert is_in_quiet_hours("09:00", "17:00", tz=KST, now=_at(8, 0)) is False


def test_overnight_window():
    # 22:00~07:00 → 23:00·03:00 제한, 12:00 비제한
    assert is_in_quiet_hours("22:00", "07:00", tz=KST, now=_at(23, 0)) is True
    assert is_in_quiet_hours("22:00", "07:00", tz=KST, now=_at(3, 0)) is True
    assert is_in_quiet_hours("22:00", "07:00", tz=KST, now=_at(12, 0)) is False


def test_boundaries_half_open():
    # [start, end): start 포함, end 제외
    assert is_in_quiet_hours("22:00", "07:00", tz=KST, now=_at(22, 0)) is True
    assert is_in_quiet_hours("22:00", "07:00", tz=KST, now=_at(7, 0)) is False


def test_all_day_when_equal():
    assert is_in_quiet_hours("00:00", "00:00", tz=KST, now=_at(15, 0)) is True


def test_is_quiet_hours_now_disabled_is_false():
    assert is_quiet_hours_now(False, "22:00", "07:00", tz=KST, now=_at(23, 0)) is False


def test_is_quiet_hours_now_enabled_delegates():
    assert is_quiet_hours_now(True, "22:00", "07:00", tz=KST, now=_at(23, 0)) is True


def test_invalid_format_is_safe_false():
    # 형식 오류 → 발송 허용(False)
    assert is_quiet_hours_now(True, "bad", "07:00", tz=KST, now=_at(23, 0)) is False
