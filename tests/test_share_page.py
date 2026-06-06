from app.services.analysis_view import Section
from app.services.share_page import render_share_html


def test_render_includes_og_meta_and_sections():
    html = render_share_html(
        title="제목임",
        headline="헤드라인임",
        one_line="한줄요약임",
        thumbnail_url="https://img/x.jpg",
        canonical_url="https://h/v/eco/abc",
        sections=[Section(key="k", title="개요", bullets=["문장1임", "문장2임"])],
        tags=["태그1"],
        published_at_kst="2026-06-06 12:00 KST",
    )
    assert '<meta property="og:title" content="헤드라인임"' in html
    assert '<meta property="og:description" content="한줄요약임"' in html
    assert '<meta property="og:image" content="https://img/x.jpg"' in html
    assert "개요" in html
    assert "문장1임" in html


def test_render_escapes_html_in_content():
    html = render_share_html(
        title="t", headline="<script>", one_line="a & b",
        thumbnail_url=None, canonical_url="https://h/v/x/y",
        sections=[], tags=[], published_at_kst="",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
