"""저신뢰도 배지 및 예약 시각 매칭 검증."""

from types import SimpleNamespace

from app.services.notify_service import build_message


def _video():
    return SimpleNamespace(title="제목", video_url="https://youtu.be/x")


def _analysis(conf):
    return SimpleNamespace(
        headline="헤드라인",
        one_line="한줄",
        short_summary_md="요약",
        sentiment="중립",
        confidence_score=conf,
    )


def test_badge_added_below_threshold():
    msg = build_message(_video(), _analysis(0.3), threshold=0.5)
    assert msg.startswith("<b>⚠️ ")


def test_no_badge_at_or_above_threshold():
    msg = build_message(_video(), _analysis(0.7), threshold=0.5)
    assert "⚠️" not in msg.split("\n")[0]


def test_no_badge_when_confidence_none():
    msg = build_message(_video(), _analysis(None), threshold=0.5)
    assert "⚠️" not in msg.split("\n")[0]
