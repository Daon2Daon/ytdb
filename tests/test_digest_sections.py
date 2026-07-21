"""Digest 섹션 레지스트리·정규화 단위 테스트."""

from __future__ import annotations

from app.services.digest_sections import (
    COMPUTED_SECTIONS,
    DEFAULT_DIGEST_SECTIONS,
    SECTION_KIND_COMPUTED,
    SECTION_KIND_LLM,
    normalize_sections,
    resolve_sections,
)


def test_default_sections_are_valid_and_neutral():
    keys = [s["key"] for s in DEFAULT_DIGEST_SECTIONS]
    assert "overview" in keys
    assert all(s["kind"] in (SECTION_KIND_LLM, SECTION_KIND_COMPUTED) for s in DEFAULT_DIGEST_SECTIONS)
    for s in DEFAULT_DIGEST_SECTIONS:
        if s["kind"] == SECTION_KIND_COMPUTED:
            assert s["key"] in COMPUTED_SECTIONS


def test_normalize_drops_invalid_kind_and_unknown_computed():
    raw = [
        {"key": "overview", "kind": "llm", "title": "요약", "guide": "핵심"},
        {"key": "bogus", "kind": "weird", "title": "x"},
        {"key": "not_a_real_computed", "kind": "computed", "title": "y"},
        {"key": "top_tags", "kind": "computed", "title": "태그"},
    ]
    out = normalize_sections(raw)
    assert [s["key"] for s in out] == ["overview", "top_tags"]
    assert out[0]["guide"] == "핵심"


def test_normalize_enforces_cap_and_defaults_title():
    raw = [{"key": f"s{i}", "kind": "llm"} for i in range(20)]
    out = normalize_sections(raw)
    assert len(out) == 12
    assert out[0]["title"]


def test_resolve_sections_falls_back():
    assert resolve_sections([], []) == DEFAULT_DIGEST_SECTIONS
    prof = [{"key": "overview", "kind": "llm", "title": "P"}]
    assert resolve_sections([], prof) == normalize_sections(prof)
    cfg = [{"key": "insights", "kind": "llm", "title": "C"}]
    assert resolve_sections(cfg, prof) == normalize_sections(cfg)


from app.services.digest_sections import build_computed_data, sections_to_markdown


class _FakeAgg:
    video_count = 3
    sentiment_breakdown = {"긍정": 2, "부정": 1}
    top_tags = [{"name": "AI", "count": 5}, {"name": "5G", "count": 2}]
    top_channels = [{"name": "KT", "count": 4}]

    class _V:
        def __init__(self, ch, head, views):
            self.channel_name = ch; self.headline = head; self.one_line = None
            self.title = None; self.view_count = views
    videos = [_V("AT&T", "네트워크 보증", 12648000), _V("KT", "AI 팝업", 5000)]


def test_build_computed_data_top_tags():
    data = build_computed_data("top_tags", _FakeAgg())
    assert data["items"][0]["name"] == "AI"


def test_build_computed_data_stats_overview():
    data = build_computed_data("stats_overview", _FakeAgg())
    assert data["video_count"] == 3


def test_build_computed_data_top_viewed_sorted():
    data = build_computed_data("top_viewed", _FakeAgg())
    assert data["items"][0]["channel"] == "AT&T"


def test_build_computed_data_unknown_key_empty():
    assert build_computed_data("nope", _FakeAgg()) == {}


def test_sections_to_markdown_renders_llm_and_computed():
    sections = [
        {"key": "overview", "kind": "llm", "title": "핵심 요약", "body_md": "- 흐름 A\n- 흐름 B"},
        {"key": "top_tags", "kind": "computed", "title": "주요 태그",
         "data": {"items": [{"name": "AI", "count": 5}]}},
    ]
    md = sections_to_markdown(sections)
    assert "## 핵심 요약" in md
    assert "흐름 A" in md
    assert "## 주요 태그" in md
    assert "AI" in md
