"""야간(지정 시간대) Telegram 발송 제한 판정.

타임존을 인자로 받아 그룹별 설정을 지원한다(youtube_monitor의 KST 고정 버전 이식).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def _minutes_from_hhmm(hhmm: str) -> int:
    parts = (hhmm or "").strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"HH:MM 형식이 아님: {hhmm!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"시각 범위 오류: {hhmm!r}")
    return hour * 60 + minute


def is_in_quiet_hours(
    start_hhmm: str,
    end_hhmm: str,
    *,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> bool:
    """현재 시각이 [start, end) 제한 구간에 포함되는지.

    - start < end: 같은 날 구간
    - start > end: 자정을 넘는 구간
    - start == end: 종일 제한
    """
    local = (now or datetime.now(tz)).astimezone(tz)
    cur = local.hour * 60 + local.minute
    start = _minutes_from_hhmm(start_hhmm)
    end = _minutes_from_hhmm(end_hhmm)
    if start == end:
        return True
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def is_quiet_hours_now(
    enabled: bool,
    start_hhmm: str,
    end_hhmm: str,
    *,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> bool:
    if not enabled:
        return False
    try:
        return is_in_quiet_hours(start_hhmm, end_hhmm, tz=tz, now=now)
    except ValueError:
        return False
