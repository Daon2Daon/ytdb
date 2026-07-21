"""Digest 섹션 레지스트리·정규화·조립 (순수 함수).

섹션 형식: {"key": str, "kind": "llm"|"computed", "title": str, "guide": str}
- llm:      LLM이 body_md(markdown)를 생성.
- computed: 집계에서 data(dict)를 생성 (LLM 불필요).
설정(config)과 산출물(digest_sections)이 같은 key/kind/title을 공유한다.
"""

from __future__ import annotations

from typing import Any

SECTION_KIND_LLM = "llm"
SECTION_KIND_COMPUTED = "computed"

MAX_SECTIONS = 12
_MAX_GUIDE_LEN = 300

# computed 섹션 레지스트리: key -> 기본 제목. 데이터는 build_computed_data가 만든다.
COMPUTED_SECTIONS: dict[str, str] = {
    "stats_overview": "이번 기간 개요",
    "sentiment_breakdown": "평가 분포",
    "top_tags": "주요 태그",
    "top_channels": "주요 채널",
    "top_viewed": "조회수 상위",
}

# 카테고리 중립 기본 세트(부트스트랩 실패·프로필 부재 시 폴백).
DEFAULT_DIGEST_SECTIONS: list[dict[str, Any]] = [
    {"key": "overview", "kind": SECTION_KIND_LLM, "title": "핵심 요약",
     "guide": "이번 기간을 가로지르는 3~5개 핵심 흐름을 개조식으로 서술"},
    {"key": "perspectives", "kind": SECTION_KIND_LLM, "title": "관점 비교",
     "guide": "합의된 관점과 엇갈리는 관점을 구분해 대비"},
    {"key": "insights", "kind": SECTION_KIND_LLM, "title": "핵심 인사이트",
     "guide": "시청자가 실제 판단에 쓸 수 있는 구체적 인사이트"},
    {"key": "top_viewed", "kind": SECTION_KIND_COMPUTED, "title": "조회수 상위"},
    {"key": "top_tags", "kind": SECTION_KIND_COMPUTED, "title": "주요 태그"},
]


def _clean(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def normalize_sections(raw: Any) -> list[dict[str, Any]]:
    """외부 입력을 검증된 섹션 배열로. 불량 항목은 drop, 상한 적용."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = _clean(item.get("key"))
        kind = _clean(item.get("kind"))
        if not key or kind not in (SECTION_KIND_LLM, SECTION_KIND_COMPUTED):
            continue
        if kind == SECTION_KIND_COMPUTED and key not in COMPUTED_SECTIONS:
            continue
        title = _clean(item.get("title"))
        if not title:
            title = COMPUTED_SECTIONS.get(key, key) if kind == SECTION_KIND_COMPUTED else key
        section: dict[str, Any] = {"key": key, "kind": kind, "title": title}
        guide = _clean(item.get("guide"))[:_MAX_GUIDE_LEN]
        if kind == SECTION_KIND_LLM and guide:
            section["guide"] = guide
        out.append(section)
        if len(out) >= MAX_SECTIONS:
            break
    return out


def resolve_sections(
    cfg_sections: Any, profile_sections: Any
) -> list[dict[str, Any]]:
    """설정 섹션 우선, 없으면 프로필 섹션, 그것도 없으면 중립 기본값."""
    cfg = normalize_sections(cfg_sections)
    if cfg:
        return cfg
    prof = normalize_sections(profile_sections)
    if prof:
        return prof
    return DEFAULT_DIGEST_SECTIONS
