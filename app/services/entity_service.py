"""데이터 평면 엔티티 사전: 자동 upsert, alias 조회, 병합 배치.

record 저장 시 entity 값을 canonical/alias로 조회해 치환·카운트하고,
미적중이면 status='auto'로 신규 등록한다(사용자 등록 대기 없음).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pg.entity import Entity


def canon_key(name: Any) -> str:
    """대소문자·양끝 공백 무시 매칭 키."""
    return str(name or "").strip().lower()


def pick_canonical_match(key: str, existing: list[dict]) -> str | None:
    """정규화 key에 대해 기존 엔티티(dict: canonical_name, aliases[]) 중 canonical 반환."""
    for e in existing:
        if canon_key(e["canonical_name"]) == key:
            return e["canonical_name"]
        for a in e.get("aliases") or []:
            if canon_key(a) == key:
                return e["canonical_name"]
    return None


async def resolve_and_register(session: AsyncSession, raw_name: str) -> str:
    """엔티티 원문 → canonical. 적중 시 카운트 갱신, 미적중 시 auto 신규 등록.

    같은 데이터 평면 세션(그룹 스키마 바인딩)에서 호출. 커밋은 호출부 책임.
    """
    key = canon_key(raw_name)
    if not key:
        return str(raw_name or "").strip()
    now = datetime.now(timezone.utc)

    rows = (await session.execute(
        select(Entity.entity_pk, Entity.canonical_name, Entity.aliases)
    )).all()
    existing = [{"entity_pk": r[0], "canonical_name": r[1], "aliases": r[2] or []} for r in rows]

    for e in existing:
        if canon_key(e["canonical_name"]) == key or any(canon_key(a) == key for a in e["aliases"]):
            await session.execute(
                update(Entity)
                .where(Entity.entity_pk == e["entity_pk"])
                .values(mention_count=Entity.mention_count + 1, last_seen=now)
            )
            return e["canonical_name"]

    canonical = str(raw_name).strip()
    await session.execute(
        Entity.__table__.insert().values(
            canonical_name=canonical, aliases=[], attrs={}, status="auto",
            mention_count=1, first_seen=now, last_seen=now,
        )
    )
    return canonical
