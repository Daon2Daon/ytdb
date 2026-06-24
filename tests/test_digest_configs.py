"""Digest 복수 설정·스케줄 단위 테스트."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.digest_config import legacy_flat_to_config, parse_digest_configs
from app.services.digest_service import (
    _most_recent_daily,
    _most_recent_monthly,
    _most_recent_weekly,
    _period,
    catch_up_window,
    compute_occurrence,
)
from app.services.settings_types import DigestScheduleConfig, period_label_from_days


def test_legacy_flat_to_config():
    cfg = legacy_flat_to_config(
        {
            "enabled": True,
            "period_weeks": 1,
            "schedule_day": "mon",
            "schedule_time": "09:30",
            "timezone": "Asia/Seoul",
            "telegram_enabled": True,
            "category": "macro",
        }
    )
    assert cfg is not None
    assert cfg.id == "legacy"
    assert cfg.name == "다이제스트"
    assert cfg.period_days == 7
    assert cfg.schedule_day == "mon"
    assert cfg.enabled is True
    assert cfg.category == "macro"


def test_parse_digest_configs_limits_and_normalizes():
    raw = [
        {"name": "일간", "period_days": 1, "schedule_time": "08:00"},
        {"name": "주간", "period_days": 99, "schedule_day": "bad", "schedule_time": "bad"},
    ]
    configs = parse_digest_configs(raw)
    assert len(configs) == 2
    assert configs[0].period_days == 1
    assert configs[1].period_days == 7
    assert configs[1].schedule_day == "sun"


def test_period_uses_days_not_weeks():
    anchor = datetime(2026, 6, 20, 20, 0, tzinfo=ZoneInfo("UTC"))
    start, end = _period(anchor, 1)
    assert (end - start).days == 1
    start7, end7 = _period(anchor, 7)
    assert (end7 - start7).days == 7


def test_most_recent_daily():
    tz = ZoneInfo("Asia/Seoul")
    now = datetime(2026, 6, 20, 10, 0, tzinfo=tz)
    occ = _most_recent_daily(now, "08:00")
    assert occ == datetime(2026, 6, 20, 8, 0, tzinfo=tz)
    now2 = datetime(2026, 6, 20, 7, 0, tzinfo=tz)
    occ2 = _most_recent_daily(now2, "08:00")
    assert occ2 == datetime(2026, 6, 19, 8, 0, tzinfo=tz)


def test_most_recent_weekly():
    tz = ZoneInfo("Asia/Seoul")
    # 2026-06-20 is Saturday
    now = datetime(2026, 6, 20, 21, 0, tzinfo=tz)
    occ = _most_recent_weekly(now, "sun", "20:00")
    assert occ == datetime(2026, 6, 14, 20, 0, tzinfo=tz)


def test_most_recent_monthly():
    tz = ZoneInfo("Asia/Seoul")
    now = datetime(2026, 6, 20, 10, 0, tzinfo=tz)
    occ = _most_recent_monthly(now, 15, "09:00")
    assert occ == datetime(2026, 6, 15, 9, 0, tzinfo=tz)
    now2 = datetime(2026, 6, 10, 8, 0, tzinfo=tz)
    occ2 = _most_recent_monthly(now2, 15, "09:00")
    assert occ2 == datetime(2026, 5, 15, 9, 0, tzinfo=tz)


def test_compute_occurrence_by_period():
    tz = ZoneInfo("Asia/Seoul")
    now = datetime(2026, 6, 20, 10, 0, tzinfo=tz)
    daily = DigestScheduleConfig(id="d", name="d", period_days=1, schedule_time="08:00")
    weekly = DigestScheduleConfig(
        id="w", name="w", period_days=7, schedule_day="sat", schedule_time="09:00"
    )
    monthly = DigestScheduleConfig(
        id="m", name="m", period_days=30, schedule_dom=20, schedule_time="09:00"
    )
    assert compute_occurrence(daily, now) == datetime(2026, 6, 20, 8, 0, tzinfo=tz)
    assert compute_occurrence(weekly, now) == datetime(2026, 6, 20, 9, 0, tzinfo=tz)
    assert compute_occurrence(monthly, now) == datetime(2026, 6, 20, 9, 0, tzinfo=tz)


def test_catch_up_window():
    assert catch_up_window(DigestScheduleConfig(id="1", name="", period_days=1)).days == 1
    assert catch_up_window(DigestScheduleConfig(id="7", name="", period_days=7)).days == 7
    assert catch_up_window(DigestScheduleConfig(id="30", name="", period_days=30)).days == 31


def test_period_label_from_days():
    assert period_label_from_days(1) == "일간"
    assert period_label_from_days(7) == "주간"
    assert period_label_from_days(30) == "월간"
