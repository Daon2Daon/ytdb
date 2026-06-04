"""저신뢰도 배지 및 예약 시각 매칭 검증."""

from types import SimpleNamespace

from app.services.notify_service import build_message


def _video():
    return SimpleNamespace(title="제목", video_url="https://youtu.be/x")


def _analysis(conf):
    return SimpleNamespace(
        headline="헤드라인", one_line="한줄", short_summary_md="요약",
        full_analysis_md="본문", bullet_points=["b1"],
        sentiment="중립", confidence_score=conf,
    )


def test_badge_added_below_threshold():
    v = SimpleNamespace(title="제목", video_url="https://youtu.be/x",
                        published_at=None, duration_seconds=None)
    a = _analysis(0.3)
    msg = build_message(v, a, threshold=0.5, detail="full")
    assert msg.startswith("⚠️ <b>[저신뢰도 분석]</b>")


def test_no_badge_at_or_above_threshold():
    v = SimpleNamespace(title="제목", video_url="https://youtu.be/x",
                        published_at=None, duration_seconds=None)
    msg = build_message(v, _analysis(0.7), threshold=0.5, detail="full")
    assert "저신뢰도 분석" not in msg


def test_no_badge_when_confidence_none():
    v = SimpleNamespace(title="제목", video_url="https://youtu.be/x",
                        published_at=None, duration_seconds=None)
    msg = build_message(v, _analysis(None), threshold=0.5, detail="full")
    assert "저신뢰도 분석" not in msg


from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.notify_service import _matches_scheduled_time

KST = ZoneInfo("Asia/Seoul")


def test_scheduled_match_exact_minute():
    now = datetime(2026, 6, 4, 14, 0, tzinfo=KST)
    assert _matches_scheduled_time(now, ["09:00", "14:00"]) is True


def test_scheduled_no_match():
    now = datetime(2026, 6, 4, 14, 1, tzinfo=KST)
    assert _matches_scheduled_time(now, ["14:00"]) is False


def test_scheduled_empty_list_false():
    now = datetime(2026, 6, 4, 14, 0, tzinfo=KST)
    assert _matches_scheduled_time(now, []) is False


def test_scheduled_ignores_bad_entries():
    now = datetime(2026, 6, 4, 14, 0, tzinfo=KST)
    assert _matches_scheduled_time(now, ["bad", "14:00"]) is True
