from app.services.analysis_view import Section, build_sections


def test_build_sections_from_structured():
    raw = [
        {"key": "overview", "title": "개요", "bullets": ["문장1", "문장2"]},
        {"key": "risk", "title": "리스크", "bullets": ["문장3"]},
    ]
    out = build_sections(raw, legacy_md=None)
    assert out == [
        Section(key="overview", title="개요", bullets=["문장1", "문장2"]),
        Section(key="risk", title="리스크", bullets=["문장3"]),
    ]


def test_build_sections_falls_back_to_legacy_markdown():
    out = build_sections(None, legacy_md="### 제목\n본문임")
    assert len(out) == 1
    assert out[0].key == "_legacy"
    assert out[0].title == ""
    assert out[0].markdown == "### 제목\n본문임"
    assert out[0].bullets == []


def test_build_sections_empty_returns_empty_list():
    assert build_sections(None, legacy_md=None) == []
    assert build_sections([], legacy_md="") == []


def test_build_sections_skips_malformed_entries():
    raw = [{"title": "no key ok", "bullets": ["a"]}, "garbage", {"bullets": []}]
    out = build_sections(raw, legacy_md=None)
    assert len(out) == 1
    assert out[0].title == "no key ok"
    assert out[0].bullets == ["a"]
