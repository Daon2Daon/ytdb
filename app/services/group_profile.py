"""그룹 프로필(app.settings category='profile') 표현·파싱 (순수)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.digest_sections import normalize_sections
from app.services.records_schema import normalize_record_schema, normalize_vocab


@dataclass
class GroupProfile:
    persona: str = ""
    digest_sections: list[dict] = field(default_factory=list)
    bootstrap_status: str = "none"   # none | done | failed
    bootstrap_at: str = ""
    record_schema: dict = field(default_factory=lambda: {"version": 1, "types": []})
    vocab: dict = field(default_factory=dict)


def parse_profile(d: dict[str, Any]) -> GroupProfile:
    return GroupProfile(
        persona=str(d.get("persona") or "").strip(),
        digest_sections=normalize_sections(d.get("digest_sections")),
        bootstrap_status=str(d.get("bootstrap_status") or "none").strip() or "none",
        bootstrap_at=str(d.get("bootstrap_at") or "").strip(),
        record_schema=normalize_record_schema(d.get("record_schema")),
        vocab=normalize_vocab(d.get("vocab")),
    )
