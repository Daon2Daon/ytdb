"""분석 결과의 정규 뷰모델.

DB의 구조화 데이터(analysis_sections) 또는 레거시 full_analysis_md를 받아
채널 무관한 단일 표현으로 정규화한다. 모든 채널 프리젠터(웹/텔레그램/SSR)는
이 뷰모델만 소비하며, 마크다운 블롭 추측 로직은 이 파일에만 존재한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass(frozen=True)
class Section:
    """본문 한 섹션. 구조화는 bullets, 레거시는 markdown으로 표현."""

    key: str
    title: str
    bullets: List[str] = field(default_factory=list)
    markdown: Optional[str] = None  # 레거시 폴백 전용


def _clean_str(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def build_sections(
    raw_sections: Any, legacy_md: Optional[str]
) -> List[Section]:
    """구조화 우선, 없으면 레거시 마크다운 단일 섹션으로 폴백."""
    if isinstance(raw_sections, list) and raw_sections:
        out: List[Section] = []
        for item in raw_sections:
            if not isinstance(item, dict):
                continue
            title = _clean_str(item.get("title"))
            bullets = [
                _clean_str(b)
                for b in (item.get("bullets") or [])
                if _clean_str(b)
            ]
            if not title and not bullets:
                continue
            out.append(
                Section(
                    key=_clean_str(item.get("key")) or "section",
                    title=title,
                    bullets=bullets,
                )
            )
        if out:
            return out
    legacy = _clean_str(legacy_md)
    if legacy:
        return [Section(key="_legacy", title="", bullets=[], markdown=legacy)]
    return []
