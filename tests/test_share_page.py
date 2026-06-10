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


from app.services.share_page import _render_markdown_min


def test_markdown_min_headings_bullets_bold():
    md = "## 주요 내용\n- 항목 하나\n- 항목 둘\n\n본문 **굵게** 끝"
    html = _render_markdown_min(md)
    assert "<h2>주요 내용</h2>" in html
    assert "<li>항목 하나</li>" in html
    assert "<li>항목 둘</li>" in html
    assert "<ul>" in html and "</ul>" in html
    assert "<strong>굵게</strong>" in html
    assert "<p>본문 <strong>굵게</strong> 끝</p>" in html


def test_markdown_min_h3_and_paragraph():
    html = _render_markdown_min("### 소제목\n그냥 문장")
    assert "<h3>소제목</h3>" in html
    assert "<p>그냥 문장</p>" in html


def test_markdown_min_escapes_html():
    html = _render_markdown_min("## <script>alert(1)</script>\n- <b>x</b>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html
