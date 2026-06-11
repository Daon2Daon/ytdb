"""digest 순수 헬퍼 검증."""

from datetime import datetime, timezone

from app.services.digest_service import (
    VideoBrief,
    split_category_tokens,
    _format_entities,
    _build_videos_block,
    _build_top_viewed_block,
    _fmt_views,
    _sentiment_summary_text,
)


def test_split_category_tokens():
    assert split_category_tokens("경제, 투자, 재테크") == ["경제", "투자", "재테크"]
    assert split_category_tokens("투자, 투자 ,재테크") == ["투자", "재테크"]
    assert split_category_tokens("") == []
    assert split_category_tokens(None) == []


def test_format_entities():
    ents = [{"type": "company", "name": "삼성전자"}, {"type": "ticker", "name": "NVDA"}]
    assert _format_entities(ents) == "삼성전자, NVDA"
    assert _format_entities(["연준", "금리"]) == "연준, 금리"
    assert _format_entities(None) == ""


def test_sentiment_summary_text():
    txt = _sentiment_summary_text({"bullish": 3, "bearish": 1})
    assert "긍정 3" in txt and "부정 1" in txt


def test_build_videos_block():
    briefs = [
        VideoBrief(channel_name="A채널", headline="헤드", one_line="한줄", title="t",
                   sentiment="bullish", bullet_points=["주장1", "주장2"],
                   insights=["인사이트1"], entities=[{"type": "company", "name": "삼성전자"}]),
    ]
    block = _build_videos_block(briefs, total=1)
    assert "[A채널] 헤드 (논조: 긍정)" in block
    assert "• 주장1" in block
    assert "▶ 인사이트: 인사이트1" in block
    assert "· 등장: 삼성전자" in block


def test_fmt_views():
    assert _fmt_views(123456) == "12.3만"
    assert _fmt_views(2500) == "2.5천"
    assert _fmt_views(800) == "800"
    assert _fmt_views(0) == ""
    assert _fmt_views(None) == ""


def test_video_meta_suffix_in_block():
    briefs = [
        VideoBrief(channel_name="SKT", headline="AI 캠페인", one_line=None, title=None,
                   sentiment="neutral", view_count=120000,
                   published_at=datetime(2026, 6, 8, tzinfo=timezone.utc)),
    ]
    block = _build_videos_block(briefs, total=1)
    assert "조회 12.0만" in block and "6/8" in block


def test_build_top_viewed_block_orders_by_views():
    briefs = [
        VideoBrief(channel_name="A", headline="low", one_line=None, title=None,
                   sentiment=None, view_count=1000),
        VideoBrief(channel_name="B", headline="high", one_line=None, title=None,
                   sentiment=None, view_count=500000),
        VideoBrief(channel_name="C", headline="none", one_line=None, title=None,
                   sentiment=None, view_count=None),
    ]
    block = _build_top_viewed_block(briefs)
    lines = block.splitlines()
    assert lines[0].startswith("- [B] high")  # 조회수 최상위 먼저
    assert "[C]" not in block  # view_count 없는 항목 제외


def test_build_top_viewed_block_empty():
    briefs = [
        VideoBrief(channel_name="A", headline="x", one_line=None, title=None,
                   sentiment=None, view_count=None),
    ]
    assert _build_top_viewed_block(briefs) == "데이터 없음"


def test_build_videos_block_remaining():
    briefs = [
        VideoBrief(channel_name=f"C{i}", headline=f"h{i}", one_line=None, title=None,
                   sentiment="neutral", bullet_points=None, insights=None, entities=None)
        for i in range(45)
    ]
    block = _build_videos_block(briefs, total=45)
    assert "외 5건" in block
