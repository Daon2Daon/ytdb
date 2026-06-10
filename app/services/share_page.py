"""공유용 매거진 HTML 렌더(서버사이드, OG 메타 포함).

뷰모델(Section 리스트 + 메타)만 받아 완성된 HTML 문자열을 반환하는 순수 함수.
React SPA와 분리된 공개 읽기전용 페이지이며, 텔레그램 등 크롤러용 OG 메타를 담는다.
"""

from __future__ import annotations

from html import escape
from typing import List, Optional

import re

from app.services.analysis_view import Section

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    """이스케이프 후 인라인 **굵게**만 <strong>으로 변환."""
    safe = escape(text)
    return _BOLD_RE.sub(r"<strong>\1</strong>", safe)


def _render_markdown_min(md: str) -> str:
    """의존성 없는 최소 마크다운 → HTML.

    지원: ## / ### 헤딩, - / • 불릿(ul로 묶음), **굵게**, 일반 문단.
    그 외 문법은 일반 텍스트로 남긴다. 모든 입력은 이스케이프한다.
    """
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out: List[str] = []
    bullets: List[str] = []

    def flush_bullets() -> None:
        if bullets:
            out.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_bullets()
            continue
        if stripped.startswith("### "):
            flush_bullets()
            out.append(f"<h3>{_inline(stripped[4:].strip())}</h3>")
        elif stripped.startswith("## "):
            flush_bullets()
            out.append(f"<h2>{_inline(stripped[3:].strip())}</h2>")
        elif stripped.startswith("- ") or stripped.startswith("• "):
            bullets.append(_inline(stripped[2:].strip()))
        else:
            flush_bullets()
            out.append(f"<p>{_inline(stripped)}</p>")
    flush_bullets()
    return "\n".join(out)


def _meta(prop: str, content: str) -> str:
    return f'<meta property="{prop}" content="{escape(content, quote=True)}">'


def _render_sections(sections: List[Section]) -> str:
    blocks = []
    for s in sections:
        if s.markdown:
            blocks.append(f'<pre class="legacy">{escape(s.markdown)}</pre>')
            continue
        items = "".join(f"<li>{escape(b)}</li>" for b in s.bullets)
        title = f"<h2>{escape(s.title)}</h2>" if s.title else ""
        blocks.append(f'<section>{title}<ul>{items}</ul></section>')
    return "\n".join(blocks)


def render_share_html(
    *,
    title: str,
    headline: Optional[str],
    one_line: Optional[str],
    thumbnail_url: Optional[str],
    canonical_url: str,
    sections: List[Section],
    tags: List[str],
    published_at_kst: str,
) -> str:
    og_title = headline or title or ""
    og_desc = one_line or ""
    metas = [
        _meta("og:title", og_title),
        _meta("og:description", og_desc),
        _meta("og:type", "article"),
        _meta("og:url", canonical_url),
    ]
    if thumbnail_url:
        metas.append(_meta("og:image", thumbnail_url))
    tag_html = ""
    if tags:
        tag_html = '<p class="tags">' + " ".join(
            f"#{escape(t)}" for t in tags
        ) + "</p>"
    hero = (
        f'<img class="hero" src="{escape(thumbnail_url, quote=True)}" alt="">'
        if thumbnail_url
        else ""
    )
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(og_title)}</title>
{chr(10).join(metas)}
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 720px;
         margin: 0 auto; padding: 1.5rem; color: #1f2937; line-height: 1.7; }}
  .hero {{ width: 100%; border-radius: 12px; margin-bottom: 1rem; }}
  h1 {{ font-size: 1.5rem; }}
  .one-line {{ color: #6b7280; font-style: italic; }}
  h2 {{ font-size: 1.15rem; margin-top: 1.8rem; }}
  ul {{ padding-left: 1.2rem; }}
  li {{ margin: .3rem 0; }}
  .tags {{ color: #2563eb; font-size: .9rem; }}
  .meta {{ color: #9ca3af; font-size: .85rem; }}
  pre.legacy {{ white-space: pre-wrap; font-family: inherit; }}
</style>
</head>
<body>
{hero}
<h1>{escape(headline or title)}</h1>
<p class="one-line">{escape(one_line or "")}</p>
<p class="meta">{escape(published_at_kst)}</p>
{_render_sections(sections)}
{tag_html}
</body>
</html>"""
