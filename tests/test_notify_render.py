from app.services.analysis_view import Section
from app.services.notify_service import _sections_to_telegram_html, _build_full


def test_sections_render_title_and_bullets_with_newlines():
    sections = [
        Section(key="overview", title="개요", bullets=["첫째임", "둘째임"]),
    ]
    out = _sections_to_telegram_html(sections)
    assert "<b>개요</b>" in out
    assert "• 첫째임\n• 둘째임" in out


def test_sections_render_inline_bold():
    sections = [Section(key="k", title="t", bullets=["**핵심**: 내용임"])]
    out = _sections_to_telegram_html(sections)
    assert "<b>핵심</b>" in out


def test_legacy_section_uses_markdown_path():
    sections = [Section(key="_legacy", title="", bullets=[], markdown="### 제목\n본문")]
    out = _sections_to_telegram_html(sections)
    assert "<b>제목</b>" in out
    assert "본문" in out


from types import SimpleNamespace


def _mk(analysis_sections=None, full_analysis_md=None):
    video = SimpleNamespace(
        title="제목", video_url="https://y/1", published_at=None,
        duration_seconds=None,
    )
    analysis = SimpleNamespace(
        headline="헤드", one_line="한줄", short_summary_md="요약",
        confidence_score=0.9, bullet_points=[],
        analysis_sections=analysis_sections, full_analysis_md=full_analysis_md,
    )
    return video, analysis


def test_build_full_uses_structured_sections():
    video, analysis = _mk(
        analysis_sections=[{"key": "k", "title": "개요", "bullets": ["첫째임"]}]
    )
    out = _build_full(video, analysis, 0.0, "채널", [])
    assert "<b>개요</b>" in out
    assert "• 첫째임" in out


def test_build_full_falls_back_to_legacy_md():
    video, analysis = _mk(full_analysis_md="### 옛제목\n옛본문")
    out = _build_full(video, analysis, 0.0, "채널", [])
    assert "<b>옛제목</b>" in out
