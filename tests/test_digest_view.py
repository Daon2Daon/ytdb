"""digest 정규 뷰모델 테스트."""

from __future__ import annotations

from app.services.digest_view import build_digest_sections


class _D:
    def __init__(self, sections=None, summary_md=None):
        self.digest_sections = sections
        self.summary_md = summary_md


def test_build_from_digest_sections():
    d = _D(sections=[
        {"key": "overview", "kind": "llm", "title": "요약", "body_md": "본문"},
        {"key": "top_tags", "kind": "computed", "title": "태그",
         "data": {"items": [{"name": "AI", "count": 3}]}},
    ])
    out = build_digest_sections(d)
    assert out[0].kind == "llm" and out[0].body_md == "본문"
    assert out[1].kind == "computed" and out[1].data["items"][0]["name"] == "AI"


def test_fallback_to_summary_md_when_no_sections():
    d = _D(sections=None, summary_md="## 요약\n- 레거시")
    out = build_digest_sections(d)
    assert len(out) == 1
    assert out[0].kind == "llm"
    assert "레거시" in out[0].body_md


def test_empty_when_nothing():
    assert build_digest_sections(_D()) == []
