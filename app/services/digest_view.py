"""Digest 산출물의 정규 뷰모델.

digest_sections(구조화) 우선, 없으면 summary_md 단일 레거시 섹션으로 폴백.
모든 프리젠터(웹/SSR)는 이 뷰모델만 소비한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class DigestSectionView:
    key: str
    kind: str
    title: str
    body_md: Optional[str] = None
    data: Optional[dict] = field(default=None)


def build_digest_sections(digest: Any) -> list[DigestSectionView]:
    raw = getattr(digest, "digest_sections", None)
    if isinstance(raw, list) and raw:
        out: list[DigestSectionView] = []
        for s in raw:
            if not isinstance(s, dict):
                continue
            out.append(DigestSectionView(
                key=str(s.get("key") or "section"),
                kind=str(s.get("kind") or "llm"),
                title=str(s.get("title") or ""),
                body_md=(str(s["body_md"]) if s.get("body_md") else None),
                data=(s.get("data") if isinstance(s.get("data"), dict) else None),
            ))
        if out:
            return out
    legacy = (getattr(digest, "summary_md", None) or "").strip()
    if legacy:
        return [DigestSectionView(key="_legacy", kind="llm", title="", body_md=legacy)]
    return []
