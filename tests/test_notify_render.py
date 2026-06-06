from app.services.analysis_view import Section
from app.services.notify_service import _sections_to_telegram_html


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
