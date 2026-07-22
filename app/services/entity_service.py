"""데이터 평면 엔티티 사전: 자동 upsert, alias 조회, 병합 배치.

record 저장 시 entity 값을 canonical/alias로 조회해 치환·카운트하고,
미적중이면 status='auto'로 신규 등록한다(사용자 등록 대기 없음).
"""

from __future__ import annotations

import json
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


_MERGE_PROMPT = """다음은 한 그룹에서 자동 수집된 엔티티 목록이다.
같은 실체를 가리키는 서로 다른 표기를 클러스터로 묶어라(예: SoftBank/소프트뱅크).
확신이 높은 것만 confidence를 high로. 애매하면 low.

## 엔티티 목록
{names}

## 출력(JSON만)
{{"clusters": [{{"canonical": "<대표표기>", "aliases": ["<흡수될 표기>"], "confidence": "high|low"}}]}}"""


def parse_merge_response(raw: str) -> tuple[list[dict], list[dict]]:
    """(auto[high], hold[그외]). 각 원소 {canonical, aliases[]}. aliases 빈 건 skip."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    auto, hold = [], []
    for c in data.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        canon = str(c.get("canonical") or "").strip()
        aliases = [str(a).strip() for a in (c.get("aliases") or []) if str(a).strip()]
        if not canon or not aliases:
            continue
        entry = {"canonical": canon, "aliases": aliases}
        if str(c.get("confidence") or "").strip().lower() == "high":
            auto.append(entry)
        else:
            hold.append(entry)
    return auto, hold


def _lower_eq(col, value):
    """대소문자 무시 컬럼 비교."""
    return func.lower(col) == str(value).strip().lower()


async def _apply_merge(session, cluster: dict) -> list[str]:
    """high confidence 클러스터 자동 병합. 병합된 alias 목록 반환(job_log 메시지용).

    canonical로 aliases 흡수: alias 엔티티 삭제, canonical.aliases 확장·status=confirmed,
    analysis_records.entity_name을 canonical로 UPDATE.
    """
    from sqlalchemy import delete as sa_delete

    from app.models.pg.analysis_record import AnalysisRecord
    canon = cluster["canonical"]
    crow = (await session.execute(
        select(Entity).where(_lower_eq(Entity.canonical_name, canon))
    )).scalars().first()
    if crow is None:
        return []
    absorbed = list(crow.aliases or [])
    merged: list[str] = []
    for alias in cluster["aliases"]:
        if canon_key(alias) == canon_key(canon):
            continue
        await session.execute(
            update(AnalysisRecord)
            .where(_lower_eq(AnalysisRecord.entity_name, alias))
            .values(entity_name=canon)
        )
        await session.execute(
            sa_delete(Entity).where(_lower_eq(Entity.canonical_name, alias))
        )
        if alias not in absorbed:
            absorbed.append(alias)
        merged.append(alias)
    await session.execute(
        update(Entity).where(Entity.entity_pk == crow.entity_pk)
        .values(aliases=absorbed, status="confirmed")
    )
    return merged


async def apply_merge_cluster(session, cluster: dict) -> list[str]:
    """수동 승인 경로 재사용 wrapper — 배치 병합과 동일 코드(설계 §3.4)."""
    return await _apply_merge(session, cluster)


async def _hold_merge(session, cluster: dict) -> None:
    """보류 후보를 canonical 엔티티 attrs.merge_candidates에 적재(Phase 3 승인 UI 입력)."""
    crow = (await session.execute(
        select(Entity).where(_lower_eq(Entity.canonical_name, cluster["canonical"]))
    )).scalars().first()
    if crow is None:
        return
    attrs = dict(crow.attrs or {})
    cands = list(attrs.get("merge_candidates") or [])
    for a in cluster["aliases"]:
        if a not in cands:
            cands.append(a)
    attrs["merge_candidates"] = cands
    await session.execute(
        update(Entity).where(Entity.entity_pk == crow.entity_pk).values(attrs=attrs)
    )


async def run_entity_merge_once() -> None:
    """전 활성 그룹 순차: 신규 auto 엔티티 있으면 경량 LLM으로 별칭 병합.

    high confidence만 자동 병합(+ job_log), 그 외는 attrs.merge_candidates로 보류.
    실패는 그룹 단위 격리(전체 배치를 멈추지 않음).
    """
    from app.control_db import get_sessionmaker
    from app.models.control.group import Group
    from app.models.pg.job_log import JobLog
    from app.services.ai_usage_service import budget_ok_for_group, record_usage
    from app.services.db_engine import data_plane_engine_manager as dpm
    from app.services.global_settings import resolve_ai_gateway
    from app.services.llm_client import LiteLLMClient

    async with get_sessionmaker()() as csess:
        groups = (await csess.execute(
            select(Group).where(Group.is_active.is_(True))
        )).scalars().all()

    for group in groups:
        try:
            await dpm.ensure_schema(group)
            async with dpm.group_session(group) as session:
                new_count = (await session.execute(
                    select(func.count()).select_from(Entity).where(Entity.status == "auto")
                )).scalar_one()
                if not new_count:
                    continue
                names = [r[0] for r in (await session.execute(
                    select(Entity.canonical_name).order_by(Entity.mention_count.desc()).limit(100)
                )).all()]
            if len(names) < 2:
                continue

            ok, _ = await budget_ok_for_group(group)
            if not ok:
                continue

            ai = await resolve_ai_gateway(group.group_id)
            model = ai.tagging_model or ai.primary_model
            client = LiteLLMClient(ai)
            try:
                chat = await client.chat(
                    model=model,
                    messages=[{"role": "user", "content": _MERGE_PROMPT.format(names=", ".join(names))}],
                    temperature=0.0,
                    max_tokens=min(ai.max_tokens or 2048, 2048),
                    response_format={"type": "json_object"},
                )
            finally:
                await client.aclose()

            await record_usage(
                user_id=group.owner_user_id, group_id=group.group_id,
                purpose="entity_merge", model=model,
                input_tokens=chat.input_tokens, output_tokens=chat.output_tokens,
            )

            auto, hold = parse_merge_response(chat.content)
            async with dpm.group_session(group) as session:
                async with session.begin():
                    for cluster in auto:
                        merged = await _apply_merge(session, cluster)
                        for alias in merged:
                            session.add(JobLog(
                                job_type="entity_merge", status="success",
                                message=f"{alias} → {cluster['canonical']}"[:500],
                            ))
                    for cluster in hold:
                        await _hold_merge(session, cluster)

                    # 이번 배치에서 검토한 auto 엔티티를 reviewed로 전환한다.
                    # 다음 틱은 이후 새로 등록된(auto) 엔티티가 있을 때만 LLM을 호출한다.
                    await session.execute(
                        update(Entity).where(Entity.status == "auto").values(status="reviewed")
                    )
        except Exception as e:  # noqa: BLE001 — 그룹 단위 격리
            print(f"[entity-merge] {getattr(group, 'slug', '?')} 실패: {e}")
