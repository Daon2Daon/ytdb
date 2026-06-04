"""텔레그램 메시지 포맷 순수 헬퍼 검증."""

from datetime import datetime, timezone

from app.services.notify_service import (
    _to_kst,
    _format_duration,
    _format_bullets,
    _truncate_html,
)


def test_to_kst_utc_to_kst():
    dt = datetime(2026, 5, 30, 2, 5, tzinfo=timezone.utc)  # 11:05 KST
    assert _to_kst(dt) == "2026-05-30 11:05 KST"


def test_format_duration_hms():
    assert _format_duration(14 * 60 + 10) == "14:10"
    assert _format_duration(3661) == "1:01:01"
    assert _format_duration(0) == ""
    assert _format_duration(None) == ""


def test_format_bullets():
    assert _format_bullets(["a", " b ", "", None]) == "• a\n• b"
    assert _format_bullets(None) == ""
    assert _format_bullets("notalist") == ""


def test_truncate_html():
    assert _truncate_html("abcdef", 100) == "abcdef"
    assert _truncate_html("abcdef", 5) == "ab..."
