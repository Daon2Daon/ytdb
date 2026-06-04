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


from types import SimpleNamespace
from app.services.notify_service import build_message


def _video(**kw):
    base = dict(title="제목", video_url="https://youtu.be/x",
                published_at=datetime(2026, 5, 30, 2, 5, tzinfo=timezone.utc),
                duration_seconds=850)
    base.update(kw)
    return SimpleNamespace(**base)


def _analysis(conf=0.9, **kw):
    base = dict(headline="헤드라인", one_line="한줄", short_summary_md="짧은요약",
                full_analysis_md="### 한 줄 요약\n본문", bullet_points=["주장1", "주장2"],
                sentiment="bullish", confidence_score=conf)
    base.update(kw)
    return SimpleNamespace(**base)


def test_full_contains_rich_fields():
    msg = build_message(_video(), _analysis(), channel_name="증시각도기TV",
                        tags=["반도체", "금리"], detail="full")
    assert "🎬 [증시각도기TV] 신규 영상" in msg
    assert "<b>헤드라인</b>" in msg
    assert "### 한 줄 요약" in msg
    assert "• 주장1" in msg
    assert "🏷 반도체, 금리" in msg
    assert "⏱ 14:10" in msg
    assert '<a href="https://youtu.be/x">영상 보러가기</a>' in msg


def test_full_low_confidence_badge_top():
    msg = build_message(_video(), _analysis(conf=0.3), threshold=0.5, detail="full")
    assert msg.startswith("⚠️ <b>[저신뢰도 분석]</b>")


def test_compact_backward_compatible():
    msg = build_message(_video(), _analysis(), detail="compact")
    assert msg.startswith("<b>헤드라인</b>")
    assert "🎬" not in msg
    assert "신뢰도" in msg


def test_full_smart_truncation_keeps_under_limit():
    big = "가" * 6000
    msg = build_message(_video(), _analysis(full_analysis_md=big),
                        channel_name="C", tags=["t"], detail="full")
    assert len(msg) <= 4096
    assert "영상 보러가기" in msg


def test_full_truncation_preserves_link_with_html_heavy_body():
    # HTML 특수문자가 많아 escape 후 폭증하는 본문 + bullets 없음 → 링크 보존 확인
    heavy = "<&>" * 3000  # escape 시 약 5배로 폭증
    a = _analysis(full_analysis_md=heavy, bullet_points=[])
    msg = build_message(_video(), a, channel_name="C", tags=["t"], detail="full")
    assert len(msg) <= 4096
    assert "영상 보러가기" in msg


def test_full_truncation_many_huge_bullets_under_limit():
    # 본문은 짧지만 거대한 bullet이 다수 → bullets를 줄여 한도 내 유지 + 링크 보존
    huge_bullets = ["가" * 500 for _ in range(20)]
    a = _analysis(full_analysis_md="짧은본문", bullet_points=huge_bullets)
    msg = build_message(_video(), a, channel_name="C", tags=["t"], detail="full")
    assert len(msg) <= 4096
    assert "영상 보러가기" in msg
