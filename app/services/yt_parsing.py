"""YouTube 메타데이터 파싱 공용 헬퍼.

monitor_service(폴링 적재)와 videos 라우터(instant 등록)가 동일한 규칙으로
published_at(ISO8601)과 duration(ISO8601 기간)을 파싱하도록 한 곳에 둔다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def parse_iso_datetime(dt_str: Optional[str]) -> datetime:
    """ISO8601 문자열을 tz-aware datetime으로. 비거나 파싱 실패 시 현재 UTC."""
    if not dt_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def parse_duration_seconds(iso_duration: Optional[str]) -> Optional[int]:
    """ISO8601 기간(PT#M#S 등)을 초로. 비거나 파싱 실패 시 None."""
    if not iso_duration:
        return None
    try:
        import isodate

        return int(isodate.parse_duration(iso_duration).total_seconds())
    except Exception:
        return None
