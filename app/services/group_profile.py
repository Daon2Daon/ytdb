"""그룹 프로필(app.settings category='profile') 표현·파싱 (순수)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.digest_sections import normalize_sections


@dataclass
class GroupProfile:
    persona: str = ""
    digest_sections: list[dict] = field(default_factory=list)
    bootstrap_status: str = "none"   # none | done | failed
    bootstrap_at: str = ""


def parse_profile(d: dict[str, Any]) -> GroupProfile:
    return GroupProfile(
        persona=str(d.get("persona") or "").strip(),
        digest_sections=normalize_sections(d.get("digest_sections")),
        bootstrap_status=str(d.get("bootstrap_status") or "none").strip() or "none",
        bootstrap_at=str(d.get("bootstrap_at") or "").strip(),
    )
